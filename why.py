"""Explain the latest decision for a post.

Usage:
    python why.py <post_id or Facebook post URL> [--user ID] [--json]
"""
import argparse
import json
import re
import sys

from apartment_bot import db as dbmod
from apartment_bot.config import ConfigError, get_user, load_config, user_config_hash
from apartment_bot.display import term_safe
from apartment_bot.human import rejected as human_rejected


def main():
    """Print the stored decision, reason, failed code checks and analysis metadata."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("post", help="post ID or URL")
    ap.add_argument("--user", help="user id from config.yaml (default: the only user)")
    ap.add_argument("--json", action="store_true", help="print the full stored analysis as UTF-8 JSON")
    args = ap.parse_args()

    m = re.search(r"(\d{6,})/?(?:\?.*)?$", args.post)
    post_id = m.group(1) if m else args.post
    try:
        user = get_user(load_config(), args.user)
    except ConfigError as e:
        sys.exit(f"config error: {e}")

    db, _ = dbmod.migrate()
    a = db.execute(
        "SELECT * FROM post_analyses WHERE post_id = ? AND user_id = ? AND status = 'ok'"
        " ORDER BY analyzed_at DESC, id DESC LIMIT 1", (post_id, user["id"])).fetchone()
    mine = human_rejected(db, user["id"]).get(post_id)   # (note, decided_at) if rejected by hand
    if mine and not args.json:
        print(f"Post {post_id}: HUMAN REJECTED - you rejected it on {mine[1][:10]}"
              + (f": {term_safe(mine[0])}" if mine[0] else "") + "  (undo: python reject.py --undo ...)")
    if not a:
        if mine:
            return 0
        known = db.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone()
        sys.exit(f"Post {post_id} " + ("has not been analyzed yet." if known else "is not in the database."))

    if args.json:
        out = dict(a)
        for k in ("facts_json", "hard_rules_json", "raw_response_json"):
            raw = out.pop(k)
            out[k[:-5]] = json.loads(raw) if raw else None   # facts/raw_response: None if rejected before AI
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"Post {post_id}: {a['final_decision']}" + (" (automatic decision, overruled by you)" if mine else ""))
    print(f"  {term_safe(a['final_reason'])}")
    print(f"  rule: {a['final_rule']}   decided by: {a['decided_by']}")
    if a["decided_by"] != "ai" or a["ai_decision"] != a["final_decision"]:
        print(f"  AI said: {a['ai_decision']} ({a['ai_rule']}): {term_safe(a['ai_reason'])}")
    for c in json.loads(a["hard_rules_json"]):
        if c["status"] in ("fail", "unverified"):
            print(f"  code check {c['rule']}: {c['status']} - {term_safe(c['detail'])}")
    stale = " (config has changed since)" if a["config_hash"] != user_config_hash(user) else ""
    print(f"  analyzed {a['analyzed_at']} with {a['model']}, pipeline {a['pipeline_version']}{stale}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
