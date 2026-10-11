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
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import db as dbmod, jobs, learning, notify, providers, settings
from .statuses import now_iso

PROJECT_DIR = Path(__file__).resolve().parent.parent
JOBS = ("download", "analyze", "all")
STOP_GRACE_SECONDS = 180
LOG_FILE = PROJECT_DIR / "scheduler.log"


class Scheduler:
    def __init__(self, cfg, user_id, db_path=dbmod.DB_PATH, runner=None, tick_seconds=60):
        self.cfg, self.user_id, self.db_path = cfg, user_id, db_path
        self.runner = runner or self._run_script
        self.tick_seconds = tick_seconds
        self.state = {"running": False, "stopping": False, "job": None, "step": None, "last_start": None, "last_end": None,
                      "last_result": None, "next_run": None}
        self._stop = threading.Event()
        self._limit_override = None   # set by a button press: how many posts this run may send to the AI
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._proc = None
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
        """Start a full run (download + analyze) at the next tick."""
        self.state["next_run"] = now_iso()
        self._force = True
        self._wake.set()

    _force = False

    def start_job(self, job="all", limit=None):
        """Start one job in the background right away: "download" (Facebook only, no AI),
        "analyze" (the AI, up to analyze_limit posts) or "all". False if one is already running."""
        if job not in JOBS:
            raise ValueError(f"unknown job {job!r}")
        if limit is not None and not 0 <= int(limit) <= 100000:
            raise ValueError("limit must be 0 (all) to 100000")
        self._limit_override = None if limit is None else int(limit)
        with self._lock:
            if self.state["running"]:
                return False
            self.state.update(running=True, job=job, step=None)
        threading.Thread(target=self.run_once, kwargs={"job": job}, name=f"job-{job}", daemon=True).start()
        return True

    def stop_job(self, grace=STOP_GRACE_SECONDS):
        """Ask the running job to stop. It finishes its current post or group, saves it and exits
        by itself. Only if it hasn't ended after `grace` seconds is its process killed (SQLite undoes
        an unfinished write, so even that cannot corrupt the database)."""
        if not self.state["running"] or self.state.get("stopping"):
            return False
        self._cancel.set()
        self.state["stopping"] = True
        jobs.request_stop()
        proc = self._proc
        if proc:
            threading.Thread(target=self._enforce_stop, args=(proc, grace), name="stop-watch", daemon=True).start()
        return True

    def _enforce_stop(self, proc, grace):
        deadline = time.time() + grace
        while proc.poll() is None and time.time() < deadline:
            jobs.request_stop()      # the script clears the file when it starts; keep asking until it ends
            time.sleep(min(1.0, grace / 4 or 0.01))
        if proc.poll() is None:
            self._log(f"stop: the job did not end within {grace}s; its process is ended now")
            _kill_tree(proc)

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
            if learning.due(db, now):
                self._learn(db, now)
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

    def run_once(self, now=None, job="all"):
        now = now or datetime.now().astimezone()
        started = now.isoformat(timespec="seconds")
        self._cancel.clear()
        jobs.clear_stop()
        self.state.update(running=True, job=job, last_start=started, step=None, stopping=False)
        result = []
        try:
            if job in ("download", "all"):
                self.state["step"] = "scan"
                code, out = self.runner(["scan_facebook.py"])
                result.append("הופסק" if self._cancel.is_set() else (_last_line(out) or f"scan exit {code}"))
            if not self._cancel.is_set():
                db = self._db()
                try:
                    conf = settings.get(db, "scheduler")
                finally:
                    db.close()
                if job == "analyze" or (job == "all" and conf.get("analyze", True)):
                    self.state["step"] = "analyze"
                    limit = int(self._limit_override if self._limit_override is not None else conf.get("analyze_limit") or 0)
                    code, out = self.runner(["analyze_posts.py", "--user", self.user_id,
                                             *(["--limit", str(limit)] if limit > 0 else ["--all"])])
                    result.append("הופסק" if self._cancel.is_set() else (_last_line(out) or f"analyze exit {code}"))
            if not self._cancel.is_set():
                self.state["step"] = "notify"
                db = self._db()
                try:
                    notify.run_price_tracking(db, self.user_id)
                    notify.collect_events(db, self.cfg, self.user_id, started)
                    notify.deliver_pending(db, self.user_id)
                finally:
                    db.close()
        finally:
            if self._cancel.is_set():
                result.append(self._integrity_note())
            jobs.clear_stop()
            end = datetime.now().astimezone()
            self.state.update(running=False, stopping=False, job=None, step=None,
                              last_end=end.isoformat(timespec="seconds"),
                              last_result=" | ".join(result),
                              next_run=(end + timedelta(minutes=self._interval())).isoformat(timespec="seconds"))
            self._proc = None
            self._log(f"{job}: " + " | ".join(result))

    def _integrity_note(self):
        """After a stop: check the database file and say so."""
        db = self._db()
        try:
            ok = db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except Exception:
            ok = False
        finally:
            db.close()
        return "מסד הנתונים תקין" if ok else "אזהרה: בדיקת מסד הנתונים נכשלה"

    def _learn(self, db, now):
        """The weekly learning run; proposals wait in the app for my decision."""
        import anthropic
        from .config import get_api_key
        try:
            router = providers.Router(db, self.cfg, lambda: anthropic.Anthropic(api_key=get_api_key(self.cfg)))
            n = learning.run(db, self.cfg, self.user_id, router, now)
            if n:
                notify.queue(db, self.user_id, "learning", f"learning:{now.date()}", "הצעות לשיפור החיפוש",
                             f"{n} הצעות חדשות להגדרות, לפי מה שסימנת. אפשר לאשר או לדחות בהגדרות.")
            self._log(f"learning: {n} proposals")
        except Exception as e:
            learning._mark_run(db, now)   # don't retry every minute
            self._log(f"learning failed: {type(e).__name__}: {e}")

    def _interval(self):
        db = self._db()
        try:
            return float(settings.get(db, "scheduler").get("interval_minutes") or 30)
        finally:
            db.close()

    def _run_script(self, args):
        """Run one of the project's scripts; its process is kept so Stop can end it."""
        proc = subprocess.Popen([sys.executable, str(PROJECT_DIR / args[0]), *args[1:]], cwd=PROJECT_DIR,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=_utf8_env())
        self._proc = proc
        try:
            out, _ = proc.communicate(timeout=6 * 3600)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            out, _ = proc.communicate()
        return proc.returncode, out or ""

    def _log(self, line):
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()} {line}\n")


def _utf8_env():
    import os
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _last_line(out):
    lines = [l for l in (out or "").strip().splitlines() if l.strip()]
    return lines[-1][:200] if lines else ""


def _kill_tree(proc):
    """End a process and everything it started (the scan starts a browser helper)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        proc.kill()
