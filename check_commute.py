"""Estimate the trip to commute.destination for every approved listing that has no estimate yet.

    python check_commute.py            # all of them (about a cent each)
    python check_commute.py --limit 5
Uses the stronger model from commute.model in config.yaml (default claude-sonnet-5-5).
"""
import argparse
import sys

import anthropic

from apartment_bot import commute, db as dbmod, providers
from apartment_bot.config import ConfigError, get_api_key, get_user, load_config


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="at most this many listings")
    args = ap.parse_args()
    try:
        cfg = load_config()
        user = get_user(cfg, None)
        key = get_api_key(cfg)
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    dest = commute.names(user)
    if not dest:
        sys.exit("Add a destination under commute.destinations in config.yaml first.")
    db, _ = dbmod.migrate()
    router = providers.Router(db, cfg, lambda: anthropic.Anthropic(api_key=key))
    ids = commute.approved_without_estimate(db, user["id"], dest)[:args.limit]
    print(f"{len(ids)} approved listings without an estimate for: {" / ".join(dest).encode('unicode_escape').decode()}")
    done = 0
    for pid in ids:
        try:
            r = commute.check(db, cfg, user, router, pid)
        except Exception as e:
            print(f"  {pid}: failed ({e})")
            continue
        done += 1
        print(f"  {pid}: {', '.join(str(x['minutes']) for x in r) if r else 'no location'}")
    print(f"Done: {done} estimated.")


if __name__ == "__main__":
    main()
