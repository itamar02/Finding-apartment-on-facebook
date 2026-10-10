"""Price tracking: the same apartment posted again (another group, a repost, a new price).

Posts are grouped into "listings" (listing_posts). A post joins an earlier listing when
  - its text is identical (the duplicate rule's fingerprint), or
  - it has the same phone number or the same author AND the text is very similar.
Each post keeps the rent from its latest analysis, so a listing has a price history; a lower
price than the listing's previous one is a price drop.
"""
import difflib
import json
import re

from .contacts import phones_in_text
from .db import EFFECTIVE_TEXT_SQL
from .hard_rules import text_fingerprint
from .textnorm import normalize

SIMILARITY = 0.8
_WS = re.compile(r"\s+")


def _clean(text):
    return normalize(text).lower()


def similar(a, b):
    m = difflib.SequenceMatcher(None, _clean(a), _clean(b), autojunk=False)
    return m.real_quick_ratio() >= SIMILARITY and m.quick_ratio() >= SIMILARITY and m.ratio() >= SIMILARITY


def _known(db):
    """Indexes over posts already grouped: fingerprint, phone and author -> [(listing, text)]."""
    by_fp, by_phone, by_author = {}, {}, {}
    rows = db.execute(f"SELECT lp.post_id, lp.listing_id, p.author_id, {EFFECTIVE_TEXT_SQL} AS text"
                      " FROM listing_posts lp JOIN posts p ON p.post_id = lp.post_id").fetchall()
    for r in rows:
        by_fp.setdefault(text_fingerprint(r["text"]), r["listing_id"])
        for ph in phones_in_text(r["text"]):
            by_phone.setdefault(ph, []).append((r["listing_id"], r["text"]))
        if r["author_id"]:
            by_author.setdefault(r["author_id"], []).append((r["listing_id"], r["text"]))
    return by_fp, by_phone, by_author


def _match(post, idx):
    by_fp, by_phone, by_author = idx
    lid = by_fp.get(text_fingerprint(post["text"]))
    if lid:
        return lid, "text"
    for ph in phones_in_text(post["text"]):
        for lid, text in by_phone.get(ph, []):
            if similar(text, post["text"]):
                return lid, "phone"
    for lid, text in by_author.get(post["author_id"], []) if post["author_id"] else []:
        if similar(text, post["text"]):
            return lid, "author_text"
    return None, "self"


def update(db, user_id, min_drop=0):
    """Group every analyzed post with a rent that isn't grouped yet (oldest first).

    Returns price drops found: [{listing_id, post_id, old_rent, new_rent}] where the new post's
    rent is at least `min_drop` below the listing's previous price.
    """
    todo = db.execute(
        f"SELECT p.post_id, p.author_id, p.posted_at, p.first_seen_at, {EFFECTIVE_TEXT_SQL} AS text, a.facts_json"
        " FROM posts p JOIN post_analyses a ON a.id = (SELECT max(b.id) FROM post_analyses b"
        "   WHERE b.post_id = p.post_id AND b.user_id = ? AND b.status = 'ok')"
        " WHERE NOT EXISTS (SELECT 1 FROM listing_posts lp WHERE lp.post_id = p.post_id)"
        " ORDER BY coalesce(p.posted_at, p.first_seen_at), p.post_id", (user_id,)).fetchall()
    if not todo:
        return []
    idx = _known(db)
    drops = []
    with db:
        for post in todo:
            facts = json.loads(post["facts_json"]) if post["facts_json"] else {}
            rent = facts.get("rent_ils")
            if not rent or not post["text"]:
                continue
            lid, how = _match(post, idx)
            if lid:
                prev = db.execute("SELECT rent_ils FROM listing_posts WHERE listing_id = ? AND rent_ils IS NOT NULL"
                                  " ORDER BY coalesce(posted_at, '') DESC, post_id DESC LIMIT 1", (lid,)).fetchone()
                if prev and prev[0] - rent >= max(1, min_drop):
                    drops.append({"listing_id": lid, "post_id": post["post_id"], "old_rent": prev[0], "new_rent": rent})
            else:
                lid = post["post_id"]
            db.execute("INSERT INTO listing_posts (post_id, listing_id, matched_by, rent_ils, posted_at)"
                       " VALUES (?,?,?,?,?)", (post["post_id"], lid, how, rent, post["posted_at"]))
            idx[0].setdefault(text_fingerprint(post["text"]), lid)
            for ph in phones_in_text(post["text"]):
                idx[1].setdefault(ph, []).append((lid, post["text"]))
            if post["author_id"]:
                idx[2].setdefault(post["author_id"], []).append((lid, post["text"]))
    return drops


def history(db, post_id):
    """Price history of the listing a post belongs to: [{post_id, rent_ils, posted_at}] oldest first.

    Empty when the post was never grouped or is the listing's only post.
    """
    row = db.execute("SELECT listing_id FROM listing_posts WHERE post_id = ?", (post_id,)).fetchone()
    if not row:
        return []
    rows = [dict(r) for r in db.execute(
        "SELECT post_id, rent_ils, posted_at, matched_by FROM listing_posts WHERE listing_id = ?"
        " ORDER BY coalesce(posted_at, ''), post_id", (row[0],))]
    return rows if len(rows) > 1 else []
