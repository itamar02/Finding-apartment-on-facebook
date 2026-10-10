"""Background work inside the app server: scan -> analyze -> notify, plus reminders.

Every minute it:
  - sends notifications that were held back (quiet hours) and queues due follow-ups;
  - when the scheduler is on and the interval has passed (and it's not the scan quiet
    hours): runs scan_facebook.py, then analyze_posts.py --all, then groups prices and
    queues notifications for what is new.
The scan and the analysis run as separate processes, exactly as from the command line, so
the scan lock and everything else behave the same. Settings: "scheduler" in app_settings.
"""
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path

from . import db as dbmod, notify, settings
from .statuses import now_iso

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = PROJECT_DIR / "scheduler.log"


class Scheduler:
    def __init__(self, cfg, user_id, db_path=dbmod.DB_PATH, runner=None, tick_seconds=60):
        self.cfg, self.user_id, self.db_path = cfg, user_id, db_path
        self.runner = runner or self._run_script
        self.tick_seconds = tick_seconds
        self.state = {"running": False, "step": None, "last_start": None, "last_end": None,
                      "last_result": None, "next_run": None}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._last_events_check = now_iso()

    # --- lifecycle ---

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()

    def run_now(self):
        """Start a run at the next tick (from the app's "scan now" button)."""
        self.state["next_run"] = now_iso()
        self._force = True
        self._wake.set()

    _force = False

    # --- work ---

    def _db(self):
        return dbmod.connect(self.db_path, check_same_thread=False)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:   # never let the thread die
                self._log(f"error: {type(e).__name__}: {e}")
            self._wake.wait(self.tick_seconds)
            self._wake.clear()

    def tick(self, now=None):
        now = now or datetime.now().astimezone()
        db = self._db()
        try:
            conf = settings.get(db, "scheduler")
            # Reminders and held-back notifications, every tick.
            since = self._last_events_check
            self._last_events_check = now.isoformat(timespec="seconds")
            notify.collect_events(db, self.cfg, self.user_id, since, now)
            notify.deliver_pending(db, self.user_id, now)
            if not (conf.get("enabled") or self._force) or self.state["running"]:
                return
            next_run = self.state["next_run"]
            if next_run and datetime.fromisoformat(next_run) > now and not self._force:
                return
            if notify.in_quiet_hours(conf.get("quiet_hours"), now) and not self._force:
                self.state["next_run"] = (now + timedelta(minutes=10)).isoformat(timespec="seconds")
                return
        finally:
            db.close()
        self._force = False
        self.run_once(now)

    def run_once(self, now=None):
        now = now or datetime.now().astimezone()
        started = now.isoformat(timespec="seconds")
        self.state.update(running=True, last_start=started, step="scan")
        result = []
        try:
            code, out = self.runner(["scan_facebook.py"])
            result.append(_last_line(out) or f"scan exit {code}")
            db = self._db()
            try:
                conf = settings.get(db, "scheduler")
            finally:
                db.close()
            if conf.get("analyze", True):
                self.state["step"] = "analyze"
                code, out = self.runner(["analyze_posts.py", "--all", "--user", self.user_id])
                result.append(_last_line(out) or f"analyze exit {code}")
            self.state["step"] = "notify"
            db = self._db()
            try:
                notify.run_price_tracking(db, self.user_id)
                notify.collect_events(db, self.cfg, self.user_id, started)
                notify.deliver_pending(db, self.user_id)
            finally:
                db.close()
        finally:
            conf_interval = self._interval()
            end = datetime.now().astimezone()
            self.state.update(running=False, step=None, last_end=end.isoformat(timespec="seconds"),
                              last_result=" | ".join(result),
                              next_run=(end + timedelta(minutes=conf_interval)).isoformat(timespec="seconds"))
            self._log("run: " + " | ".join(result))

    def _interval(self):
        db = self._db()
        try:
            return float(settings.get(db, "scheduler").get("interval_minutes") or 30)
        finally:
            db.close()

    def _run_script(self, args):
        proc = subprocess.run([sys.executable, str(PROJECT_DIR / args[0]), *args[1:]], cwd=PROJECT_DIR,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              env=_utf8_env(), timeout=6 * 3600)
        return proc.returncode, proc.stdout + proc.stderr

    def _log(self, line):
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {line}\n")


def _utf8_env():
    import os
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _last_line(out):
    lines = [l for l in (out or "").strip().splitlines() if l.strip()]
    return lines[-1][:200] if lines else ""
