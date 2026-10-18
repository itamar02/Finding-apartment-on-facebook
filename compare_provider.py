"""Check a free AI provider against Claude's saved answers on old posts (the gate question).

    python compare_provider.py --provider openrouter --posts 100

Takes posts Claude already analyzed, asks the provider the gate question about them (10 per request), and
reports how often it agrees: "is it a rental offer?" and the listing type. The costly mistake is a *good* post
the provider would reject. Nothing is saved to the database.
"""
import argparse
import sys

import anthropic

from apartment_bot import ai_filter, providers, db as dbmod
from apartment_bot.config import ConfigError, get_api_key, get_user, load_config


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--provider", default="openrouter")
    ap.add_argument("--posts", type=int, default=100)
    ap.add_argument("--batch", type=int, default=10)
    args = ap.parse_args()
    try:
        cfg = load_config()
        user = get_user(cfg, None)
        key = get_api_key(cfg)
    except ConfigError as e:
        sys.exit(f"config error: {e}")
    db, _ = dbmod.migrate()
    router = providers.Router(db, cfg, lambda: anthropic.Anthropic(api_key=key))
    conf = router.confs.get(args.provider)
    if not conf or (conf.get("kind") != "claude_code" and not router.keys.get(args.provider)):
        sys.exit(f"Provider {args.provider!r} has no API key yet: save it in the app (Settings > AI providers).")
    router.stages["gate"] = args.provider        # for this run only

    rows = db.execute(
        "SELECT p.post_id, p.text, p.posted_at, a.facts_json FROM posts p JOIN post_analyses a ON a.id = ("
        " SELECT max(b.id) FROM post_analyses b WHERE b.post_id = p.post_id AND b.user_id = ? AND b.status = 'ok')"
        " WHERE a.model NOT LIKE 'none%' AND a.facts_json IS NOT NULL AND length(p.text) > 40"
        " ORDER BY random() LIMIT ?", (user["id"], args.posts)).fetchall()
    import json
    from datetime import date
    asked = rental_agree = type_agree = good_rejected = bad_let_in = skipped = 0
    for start in range(0, len(rows), args.batch):
        chunk = rows[start:start + args.batch]
        try:
            gates, *_ = ai_filter.analyze_gate_batch(router, user, [(r["text"], r["posted_at"]) for r in chunk],
                                                     date.today().isoformat())
        except Exception as e:
            print(f"request failed: {e}")
            skipped += len(chunk)
            continue
        for r, g in zip(chunk, gates):
            if g is None:
                skipped += 1
                continue
            truth = json.loads(r["facts_json"])
            asked += 1
            ok_rental = bool(g["is_rental_offer"]) == bool(truth.get("is_rental_offer"))
            rental_agree += ok_rental
            type_agree += g["listing_type"] == truth.get("listing_type")
            if truth.get("is_rental_offer") and not g["is_rental_offer"]:
                good_rejected += 1
            if not truth.get("is_rental_offer") and g["is_rental_offer"]:
                bad_let_in += 1
    print(f"Asked about {asked} posts ({skipped} unanswered)." if asked else "No answers.")
    if asked:
        print(f"'Is it a rental offer?' agrees with Claude: {rental_agree}/{asked} ({100 * rental_agree // asked}%)")
        print(f"Listing type agrees: {type_agree}/{asked} ({100 * type_agree // asked}%)")
        print(f"Rentals it would wrongly reject: {good_rejected}; non-rentals it would let through: {bad_let_in}")
    for n in router.notes:
        print("note:", n)


if __name__ == "__main__":
    main()
