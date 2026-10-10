"""Find Facebook groups: the groups you are a member of, and group search results.

Usage:
    python discover_groups.py                       # your groups + the search queries from the app
    python discover_groups.py --query "שותפים חיפה"  # only these searches (repeatable)
    python discover_groups.py --no-my-groups        # searches only
    python discover_groups.py --score               # also rate relevance with the AI (cheap model)

Read-only and at a human pace, in the bot's Chrome, under the same lock as the scanner (so
it never runs at the same time as a scan). It never joins anything: groups you are not a
member of get a "join on Facebook" link in the app. Found groups are added unselected;
pick the ones to scan in the app's Groups screen.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import bot_chrome
import scan_facebook
from apartment_bot import db as dbmod, groups as groups_mod, settings
from apartment_bot.config import ConfigError, get_user, load_config

HERE = Path(__file__).resolve().parent
HARNESS_SCRIPT = HERE / "fb_harness_discover.py"
RESULT_MARKER = "@@FB_DISCOVER_RESULT@@"


def run_harness(queries, my_groups, max_per_query, env):
    conf = {"queries": queries, "my_groups": my_groups, "max_per_query": max_per_query}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(conf, f)
        path = f.name
    env = dict(os.environ, FB_DISCOVER_CONFIG=path, PYTHONIOENCODING="utf-8", **env)
    try:
        proc = subprocess.run([scan_facebook.find_browser_harness()], input=HARNESS_SCRIPT.read_bytes(),
                              capture_output=True, env=env, timeout=1800)
    except subprocess.TimeoutExpired:
        return {"my_groups": [], "searches": [], "error": "browser-harness timed out after 30 minutes"}
    finally:
        os.unlink(path)
    for line in proc.stdout.decode("utf-8", "replace").splitlines():
        if line.startswith(RESULT_MARKER):
            return json.loads(line[len(RESULT_MARKER):])
    err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
    return {"my_groups": [], "searches": [],
            "error": "browser-harness failed: " + (err[-1] if err else f"exit code {proc.returncode}")}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--query", action="append", help="search for groups with this text (repeatable)")
    ap.add_argument("--no-my-groups", action="store_true", help="skip the list of groups you are in")
    ap.add_argument("--max-per-query", type=int, default=30)
    ap.add_argument("--score", action="store_true", help="rate each group's relevance with the AI")
    ap.add_argument("--user", help="user id from config.yaml (default: the only user)")
    args = ap.parse_args()
    try:
        cfg = load_config()
        user = get_user(cfg, args.user)
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    db, _ = dbmod.migrate()
    groups_mod.seed_from_config(db, cfg)
    queries = args.query or settings.group_queries(db, user)

    scan_facebook.acquire_lock()
    try:
        try:
            if bot_chrome.ensure_running(cfg):
                print("Started the bot Chrome.", flush=True)
        except RuntimeError as e:
            sys.exit(f"Could not start the bot Chrome: {e}")
        print(f"Looking for groups ({'your groups + ' if not args.no_my_groups else ''}{len(queries)} searches)...",
              flush=True)
        result = run_harness(queries, not args.no_my_groups, args.max_per_query, bot_chrome.harness_env(cfg))
    finally:
        scan_facebook.LOCK_PATH.unlink(missing_ok=True)

    added = updated = 0
    a, u = groups_mod.upsert_discovered(db, result["my_groups"], "my_groups")
    added, updated = added + a, updated + u
    for s in result["searches"]:
        a, u = groups_mod.upsert_discovered(db, s["groups"], "search", s["query"])
        added, updated = added + a, updated + u
    print(f"Your groups: {len(result['my_groups'])}. Search results: "
          f"{sum(len(s['groups']) for s in result['searches'])}. New groups: {added}, updated: {updated}.")
    if result["error"]:
        print(f"Stopped early: {result['error']}")
    if args.score:
        from apartment_bot import group_score
        try:
            n, cost = group_score.score_unscored(db, cfg, user)
            print(f"Rated {n} groups (${cost:.4f}).")
        except ConfigError as e:
            print(f"Rating skipped: {e}")
    return 1 if result["error"] and not (result["my_groups"] or result["searches"]) else 0


if __name__ == "__main__":
    sys.exit(main())
