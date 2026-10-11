"""A polite "please stop" for the long jobs (scan, AI analysis).

The app's Stop button creates the file `.stop` in the project folder. scan_facebook.py and
analyze_posts.py look for it between posts / groups: they finish what they are doing, save
it, and exit on their own. Nothing is killed in the middle of a database write (SQLite also
undoes an unfinished write after a crash, so even a forced stop doesn't corrupt it).
Each script removes a stale file when it starts.
"""
from pathlib import Path

STOP_FILE = Path(__file__).resolve().parent.parent / ".stop"


def request_stop():
    STOP_FILE.write_text("stop", encoding="utf-8")


def stop_requested():
    return STOP_FILE.exists()


def clear_stop():
    STOP_FILE.unlink(missing_ok=True)


# --- who is running: lock files with the process id (so a restarted server still sees a running job) ---

import os
import subprocess

SCAN_LOCK = STOP_FILE.parent / ".scan.lock"          # written by scan_facebook.py
ANALYZE_LOCK = STOP_FILE.parent / ".analyze.lock"    # written by analyze_posts.py


def _pid_alive(pid):
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_pid(path):
    try:
        text = Path(path).read_text().strip()
    except OSError:
        return None
    return int(text) if text.isdigit() else None


def external_job():
    """"scan" / "analyze" if such a job is alive (even one a previous server started), else None."""
    for name, path in (("scan", SCAN_LOCK), ("analyze", ANALYZE_LOCK)):
        pid = _lock_pid(path)
        if pid and _pid_alive(pid):
            return name
    return None


def acquire_analyze_lock():
    """Take the analysis lock; False if another analysis is alive. A lock left by a dead process is replaced."""
    for _ in range(2):
        try:
            fd = os.open(ANALYZE_LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            pid = _lock_pid(ANALYZE_LOCK)
            if pid and _pid_alive(pid):
                return False
            ANALYZE_LOCK.unlink(missing_ok=True)
            continue
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    return False


def release_analyze_lock():
    if _lock_pid(ANALYZE_LOCK) == os.getpid():
        ANALYZE_LOCK.unlink(missing_ok=True)
