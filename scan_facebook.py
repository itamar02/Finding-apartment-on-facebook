"""Scan the configured Facebook groups for new posts and store them in SQLite.

Usage:
    python scan_facebook.py [--group ID ...] [--max-posts N] [--stop-after-known N] [--max-age-days N]

Attaches to the local logged-in Chrome via browser-harness and, for each group in
config.yaml (facebook.groups), reads the feed sorted by "New posts" and inserts posts
whose Facebook post ID is not yet in the database. Post bodies are never printed.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from apartment_bot.config import ConfigError, load_config, max_post_age_days
from apartment_bot.db import POST_IMAGES_DDL
import bot_chrome
from fb_time import parse_fb_timestamp

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "apartments.db"
HARNESS_SCRIPT = HERE / "fb_harness_scan.py"
RESULT_MARKER = "@@FB_SCAN_RESULT@@"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    post_id        TEXT PRIMARY KEY,
    group_id       TEXT NOT NULL,
    author         TEXT,
    author_id      TEXT,
    posted_at      TEXT,  -- ISO 8601 with local UTC offset, NULL if unknown
    posted_at_raw  TEXT,  -- Facebook's tooltip text, e.g. "Friday, October 2, 2026 at 5:50 PM"
    text           TEXT,
    text_truncated INTEGER NOT NULL DEFAULT 0,
    url            TEXT NOT NULL,
    first_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_posts_group_posted ON posts(group_id, posted_at);
CREATE TABLE IF NOT EXISTS scan_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    scanned       INTEGER,
    new_posts     INTEGER,
    unidentified  INTEGER,
    stop_reason   TEXT,
    sort_label    TEXT,
    error         TEXT
);
""" + POST_IMAGES_DDL


def now_iso():
    """Current local time as ISO 8601 with UTC offset, e.g. 2026-10-02T21:20:24+03:00."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def find_browser_harness():
    """Path to the browser-harness CLI (PATH first, then uv's default tool bin dir)."""
    exe = shutil.which("browser-harness")
    if exe:
        return exe
    fallback = Path.home() / ".local" / "bin" / "browser-harness.exe"
    if fallback.exists():
        return str(fallback)
    sys.exit("browser-harness not found. Install it with: uv tool install browser-harness")


def run_harness(group_id, known_ids, max_posts, stop_after_known, cutoff_iso, harness_env):
    """Run fb_harness_scan.py inside browser-harness for one group; return its result dict.

    The settings go through a temp JSON file named in $FB_SCAN_CONFIG; the script's
    stdin is the harness script itself. The result is the stdout line prefixed with
    RESULT_MARKER. Failures are returned as {"error": ...} so other groups still run.
    """
    cfg = {"group_id": group_id, "known_ids": sorted(known_ids), "max_posts": max_posts,
           "stop_after_known": stop_after_known, "cutoff_iso": cutoff_iso, "project_dir": str(HERE)}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(cfg, f)
        cfg_path = f.name
    env = dict(os.environ, FB_SCAN_CONFIG=cfg_path, PYTHONIOENCODING="utf-8", **harness_env)
    try:
        proc = subprocess.run([find_browser_harness()], input=HARNESS_SCRIPT.read_bytes(),
                              capture_output=True, env=env, timeout=1800)
    except subprocess.TimeoutExpired:
        proc = None
    finally:
        os.unlink(cfg_path)
    empty = {"posts": [], "unidentified": 0, "stop_reason": None, "sort_label": None}
    if proc is None:
        return {**empty, "error": "browser-harness timed out after 30 minutes"}
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER):])
    err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return {**empty, "error": "browser-harness failed: " + (err[-1] if err else f"exit code {proc.returncode}")}


def store_result(db, group_id, result, started):
    """Insert unseen posts and log the run. Returns the number of new posts."""
    new = 0
    seen_at = now_iso()
    with db:
        for p in result["posts"]:
            cur = db.execute(
                "INSERT OR IGNORE INTO posts (post_id, group_id, author, author_id, posted_at, posted_at_raw,"
                " text, text_truncated, url, first_seen_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (p["post_id"], group_id, p["author"], p["author_id"], parse_fb_timestamp(p["timestamp_raw"]),
                 p["timestamp_raw"], p["text"], int(bool(p["truncated"])),
                 f"https://www.facebook.com/groups/{group_id}/posts/{p['post_id']}/", seen_at))
            new += cur.rowcount
            if cur.rowcount:
                db.executemany(
                    "INSERT OR IGNORE INTO post_images (post_id, idx, url, alt, more_hidden) VALUES (?,?,?,?,?)",
                    [(p["post_id"], i, ph["url"], ph["alt"], p.get("more_photos") or 0)
                     for i, ph in enumerate(p.get("photos") or [], 1)])
        db.execute(
            "INSERT INTO scan_runs (group_id, started_at, finished_at, scanned, new_posts, unidentified,"
            " stop_reason, sort_label, error) VALUES (?,?,?,?,?,?,?,?,?)",
            (group_id, started, now_iso(), len(result["posts"]), new, result["unidentified"],
             result["stop_reason"], result["sort_label"], result["error"]))
    return new


def group_is_complete(db, group_id):
    """True if the group's last run finished normally (no error).

    Then meeting already-stored posts means everything older is stored too. A run cut
    short by an error leaves a gap, so the next run reads back to the age limit again.
    A run that hit --max-posts counts as finished: busy groups would otherwise re-read
    the cap on every run; only the first run's backlog beyond the cap is skipped.
    """
    row = db.execute("SELECT stop_reason, error FROM scan_runs WHERE group_id = ? ORDER BY id DESC LIMIT 1",
                     (group_id,)).fetchone()
    return bool(row) and row[0] is not None and not row[1]


LOCK_PATH = HERE / ".scan.lock"


def acquire_lock():
    """Refuse to run two scans at once - they would fight over the same browser tab."""
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        pid = LOCK_PATH.read_text().strip() or "?"
        if pid.isdigit() and not _pid_alive(int(pid)):
            LOCK_PATH.unlink()             # left over from a crashed scan
            return acquire_lock()
        sys.exit(f"Another scan is already running (process {pid}). Wait for it to finish.")
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


def _pid_alive(pid):
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def main():
    """Scan each configured group, insert unseen posts, print progress and a summary."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--group", action="append", help="only scan these group ids (repeatable)")
    ap.add_argument("--max-posts", type=int, default=300,
                    help="safety cap on posts read per group per run (default 300); normally a "
                         "group stops at posts older than the age limit first")
    ap.add_argument("--stop-after-known", type=int, default=3,
                    help="stop a group after this many consecutive already-stored posts (default 3)")
    ap.add_argument("--full", action="store_true",
                    help="read every group back to the age limit, even past already-stored posts")
    ap.add_argument("--max-age-days", type=float,
                    help="stop a group once posts are older than this (default: largest "
                         "max_post_age_days in config.yaml)")
    args = ap.parse_args()

    try:
        cfg = load_config()
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    groups = [g["id"] for g in cfg["facebook"]["groups"]]
    if args.group:
        unknown = set(args.group) - set(groups)
        if unknown:
            sys.exit(f"not in config.yaml facebook.groups: {', '.join(sorted(unknown))}")
        groups = [g for g in groups if g in args.group]
    max_age = args.max_age_days if args.max_age_days is not None else max_post_age_days(cfg)
    cutoff = (datetime.now().astimezone() - timedelta(days=max_age)).isoformat(timespec="minutes")

    acquire_lock()
    try:
        return _scan(args, cfg, groups, cutoff)
    finally:
        LOCK_PATH.unlink(missing_ok=True)


def _scan(args, cfg, groups, cutoff):
    """The scan itself (main() holds the lock around it)."""
    # Scans run in the bot's own Chrome (bot_chrome.py), started here if needed.
    try:
        if bot_chrome.ensure_running(cfg):
            print("Started the bot Chrome.", flush=True)
    except RuntimeError as e:
        sys.exit(f"Could not start the bot Chrome: {e}")
    env = bot_chrome.harness_env(cfg)

    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    total_scanned = total_new = 0
    failed = []
    for i, group_id in enumerate(groups, 1):
        known = set()
        if not args.full and group_is_complete(db, group_id):
            # Everything back to the age limit was read before, so meeting stored posts
            # means the rest is already here. Otherwise read on to the age limit.
            known = {r[0] for r in db.execute("SELECT post_id FROM posts WHERE group_id = ?", (group_id,))}
        started = now_iso()
        result = run_harness(group_id, known, args.max_posts, args.stop_after_known, cutoff, env)
        new = store_result(db, group_id, result, started)
        total_scanned += len(result["posts"])
        total_new += new
        line = f"[{i}/{len(groups)}] {group_id}: scanned {len(result['posts'])}, new {new}"
        if result["error"]:
            failed.append(group_id)
            line += f"  ERROR: {result['error']}"
        print(line, flush=True)
        if result["error"] and "not logged in" in result["error"]:
            print("Stopped: Facebook is not logged in in the bot Chrome. Run: python bot_chrome.py "
                  "and log in there, then scan again.")
            break
        if result["error"] and "timed out" in result["error"] and not result["posts"]:
            # Chrome draws nothing while its window is minimized or fully covered, so every
            # following group would fail the same way.
            print("Stopped: Chrome is not rendering the page. Keep the Chrome window visible "
                  "(not minimized or fully covered) while scanning, then run again.")
            break
    db.close()

    summary = f"Scanned {total_scanned} posts in {len(groups)} groups, found {total_new} new posts."
    if failed:
        summary += f" {len(failed)} groups had errors (see scan_runs)."
    print(summary)
    return 1 if failed and not total_scanned else 0


if __name__ == "__main__":
    sys.exit(main())
