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
