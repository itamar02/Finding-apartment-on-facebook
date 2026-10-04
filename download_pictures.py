"""Download photos of listings that passed the filter (and retry failed downloads).

Usage:
    python download_pictures.py                       # all qualifying listings
    python download_pictures.py --post-id ID          # only these listings (repeatable)
    python download_pictures.py --post-id ID --ignore-decision   # regardless of decision

analyze_posts.py already downloads photos right after analysis; use this to retry or
after adding a decision to pictures.save_for in config.yaml.
"""
import argparse
import sys

from apartment_bot import db as dbmod
from apartment_bot.config import ConfigError, load_config
from apartment_bot.pictures import download_pictures, download_post_pictures


def main():
    """Download pending photos and print a one-line summary."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--post-id", action="append", help="only these post IDs (repeatable)")
    ap.add_argument("--ignore-decision", action="store_true",
                    help="with --post-id: download even if the post did not pass")
    args = ap.parse_args()
    if args.ignore_decision and not args.post_id:
        sys.exit("--ignore-decision needs --post-id")
    try:
        cfg = load_config()
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    db, _ = dbmod.migrate()

    if args.ignore_decision:
        stats = {"posts": 0, "downloaded": 0, "failed": 0}
        for pid in args.post_id:
            ok, failed = download_post_pictures(db, cfg, pid)
            stats["posts"] += bool(ok or failed)
            stats["downloaded"] += ok
            stats["failed"] += failed
    else:
        stats = download_pictures(db, cfg, post_ids=args.post_id)
    print(f"Downloaded {stats['downloaded']} photos for {stats['posts']} listings"
          + (f", {stats['failed']} failed (see post_images.download_error)." if stats["failed"] else "."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
