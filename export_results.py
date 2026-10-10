"""Export stored results to a file. Read-only: no AI calls, no Facebook access, no cost.

Usage:
    python export_results.py results.txt     # readable report: passed / review / ---- / rejected
    python export_results.py results.json    # same, as JSON with passed/needs_review/rejected sections
    python export_results.py results.txt --user friend1
    python export_results.py results.txt --all-dates   # also listings older than the recent window

By default only recent listings are exported: posted within the app's "recent" window
(the largest listing.max_post_age_days in config.yaml, unless changed in the app).
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from apartment_bot import db as dbmod, settings
from apartment_bot.config import ConfigError, get_user, load_config
from apartment_bot.pipeline import recheck_stored
from apartment_bot.report import build_entries, write_report


def main():
    """Write the report and print a one-line summary."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", type=Path, help="output file (.json for JSON, anything else for text)")
    ap.add_argument("--user", help="user id from config.yaml (default: the only user)")
    ap.add_argument("--all-dates", action="store_true", help="include listings older than the recent window")
    args = ap.parse_args()
    try:
        cfg = load_config()
        user = get_user(cfg, args.user)
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    db, _ = dbmod.migrate()
    changed = recheck_stored(db, user)   # free: duplicates found later, listings that became too old
    if changed:
        print(f"Re-checked saved results: {changed} listings are now rejected (duplicate / too old).")
    entries = build_entries(db, cfg, user["id"])
    if not args.all_dates:
        days = settings.recent_days(db, cfg)
        cutoff = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds")
        older = [e for e in entries if e["posted_at"] and e["posted_at"] < cutoff]
        entries = [e for e in entries if not e["posted_at"] or e["posted_at"] >= cutoff]
        if older:
            print(f"Left out {len(older)} listings posted more than {days:g} days ago (use --all-dates to include them).")
    write_report(entries, args.file, user["id"])
    counts = {d: sum(e["decision"] == d for e in entries) for d in ("PASS", "NEEDS_REVIEW", "HUMAN_REJECTED", "REJECT")}
    print(f"Wrote {len(entries)} listings to {args.file} (passed {counts['PASS']}, "
          f"needs review {counts['NEEDS_REVIEW']}, rejected by you {counts['HUMAN_REJECTED']}, "
          f"rejected automatically {counts['REJECT']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
