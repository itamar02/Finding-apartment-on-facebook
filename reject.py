"""Reject listings by hand (e.g. because you already looked at them).

Usage:
    python reject.py <post id or link> [<post id or link> ...] [--note "why"]
    python reject.py --undo <post id or link> [...]     # take a rejection back
    python reject.py --list                             # everything you rejected so far

Take the post link from the "Link:" line of an exported report. Rejected listings move to
the HUMAN REJECTED section of every export, are skipped by the AI (no cost), and stay
rejected even if the analysis is re-run. Nothing is deleted - the post, its analysis and
its photos stay where they are.
"""
import argparse
import sys

from apartment_bot import db as dbmod, human
from apartment_bot.config import ConfigError, get_user, load_config
from apartment_bot.display import term_safe


def main():
    """Reject, un-reject or list human rejections; print a short summary."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("posts", nargs="*", help="post IDs or Facebook post links")
    ap.add_argument("--note", help="optional reason, saved with the rejection")
    ap.add_argument("--undo", action="store_true", help="remove the rejection of these posts")
    ap.add_argument("--list", action="store_true", help="show everything you rejected")
    ap.add_argument("--user", help="user id from config.yaml (default: the only user)")
    args = ap.parse_args()
    try:
        user = get_user(load_config(), args.user)
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    db, _ = dbmod.migrate()

    if args.list:
        mine = human.rejected(db, user["id"])
        for pid, (note, when) in sorted(mine.items(), key=lambda kv: kv[1][1], reverse=True):
            print(f"{pid}  {when[:16].replace('T', ' ')}  {term_safe(note) if note else ''}")
        print(f"{len(mine)} listings rejected by you.")
        return 0
    if not args.posts:
        ap.error("give at least one post ID or link (or use --list)")

    ids, bad = [], []
    for text in args.posts:
        pid = human.parse_post_id(text)
        (ids if pid else bad).append(pid or text)
    if bad:
        sys.exit("could not find a post ID in: " + ", ".join(bad))

    if args.undo:
        n = human.undo(db, user["id"], ids)
        print(f"Removed {n} human rejections; those listings are back to their automatic decision.")
        return 0
    done, unknown = human.reject(db, user["id"], ids, args.note)
    if done:
        print(f"Rejected {len(done)} listings. They now appear under HUMAN REJECTED in exports.")
    if unknown:
        print("Not in the database (nothing changed): " + ", ".join(unknown))
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main())
