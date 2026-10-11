"""Posts for the phone app: one card per post with the AI verdict, my status and contacts.

Read-only over `posts` and `post_analyses` (the AI verdict is derived, never copied). Uses
the same "latest successful analysis per post and user" rule as report.py.
"""
import json
from datetime import datetime, timedelta

from . import groups as groups_mod, outreach, prices, settings, statuses
from .contacts import contact_info
from .db import EFFECTIVE_TEXT_SQL

DECISIONS = ("PASS", "NEEDS_REVIEW", "REJECT", "PENDING")   # PENDING: not analyzed yet
SORTS = ("newest", "price_asc", "price_desc")
CARD_FACTS = ("listing_type", "rent_ils", "total_monthly_ils_known", "city", "neighborhood", "street",
              "location_summary_en", "rooms", "size_sqm", "floor", "move_in_date", "furnished", "broker")

CLOSED_STATUSES = ("not_relevant", "taken")

_BASE_SQL = """
SELECT p.post_id, p.group_id, p.author, p.author_id, p.posted_at, {text} AS text, p.text_truncated, p.url,
       p.first_seen_at,
       (SELECT count(*) FROM post_versions v WHERE v.post_id = p.post_id AND v.kind = 'edit') AS edits,
       a.final_decision, a.final_reason, a.final_rule, a.decided_by, a.facts_json, a.analyzed_at,
       s.status, s.note AS status_note, s.source AS status_source, s.updated_at AS status_updated_at,
       (SELECT count(*) FROM post_notes n WHERE n.post_id = p.post_id AND n.user_id = :user) AS notes_count,
       l.location AS my_location, pr.rent_ils AS my_rent
FROM posts p
LEFT JOIN post_location l ON l.post_id = p.post_id AND l.user_id = :user
LEFT JOIN post_price pr ON pr.post_id = p.post_id AND pr.user_id = :user
LEFT JOIN post_analyses a ON a.id = (
    SELECT max(b.id) FROM post_analyses b
    WHERE b.post_id = p.post_id AND b.user_id = :user AND b.status = 'ok')
LEFT JOIN post_status s ON s.post_id = p.post_id AND s.user_id = :user
WHERE coalesce(p.text, '') != ''
"""


def group_names(db, cfg):
    return groups_mod.names(db, cfg)


def query_posts(db, cfg, user_id, *, decisions=None, statuses_=None, exclude_statuses=None, with_status=False, groups=None, price_min=None,
                price_max=None, area=None, since=None, until=None, include_old=False, has_phone=None,
                status_source=None, sort="newest", offset=0, limit=30, post_ids=None):
    """Filtered, sorted cards. Returns {"total": n, "items": [card, ...]}.

    statuses_ may contain "none" for posts without a status of mine. Posts older than the
    recent window (settings.recent_days) are left out unless include_old or `since` is given.
    """
    sql, params = _BASE_SQL.format(text=EFFECTIVE_TEXT_SQL), {"user": user_id}
    if post_ids is not None:
        sql += f" AND p.post_id IN ({','.join(f':p{i}' for i in range(len(post_ids)))})"
        params.update({f"p{i}": pid for i, pid in enumerate(post_ids)})
    if since:
        sql += " AND p.posted_at >= :since"
        params["since"] = since
    elif not include_old and post_ids is None:
        cutoff = datetime.now().astimezone() - timedelta(days=settings.recent_days(db, cfg))
        sql += " AND coalesce(p.posted_at, p.first_seen_at) >= :cutoff"
        params["cutoff"] = cutoff.isoformat(timespec="seconds")
    if until:
        sql += " AND p.posted_at < :until"
        params["until"] = until
    if groups:
        sql += f" AND p.group_id IN ({','.join(f':g{i}' for i in range(len(groups)))})"
        params.update({f"g{i}": g for i, g in enumerate(groups)})
    rows = db.execute(sql, params).fetchall()

    names = group_names(db, cfg)
    template = settings.get(db, "whatsapp_template")
    cards = []
    for r in rows:
        decision = r["final_decision"] or "PENDING"
        # with_status: a listing I (or an AI chat) gave an open status stays in the list whatever the AI said.
        keeps_status = with_status and r["status"] and r["status"] not in CLOSED_STATUSES
        if decisions and decision not in decisions and not keeps_status:
            continue
        if statuses_ and (r["status"] or "none") not in statuses_:
            continue
        if exclude_statuses and r["status"] in exclude_statuses:
            continue
        if status_source and r["status_source"] != status_source:
            continue
        facts = json.loads(r["facts_json"]) if r["facts_json"] else None
        if r["my_rent"]:   # the price I typed replaces the AI's reading everywhere (filters, sorting, the card)
            facts = {**(facts or {}), "_ai_rent_ils": (facts or {}).get("rent_ils"), "rent_ils": r["my_rent"],
                     "total_monthly_ils_known": None, "_rent_mine": True}
        rent = facts.get("rent_ils") if facts else None
        if price_min is not None and (rent is None or rent < price_min):
            continue
        if price_max is not None and (rent is None or rent > price_max):
            continue
        if area and not _matches_area(facts, area, r["my_location"]):
            continue
        contact = contact_info(r)
        if has_phone is not None and bool(contact["phones"] or contact["whatsapp"]) != has_phone:
            continue
        cards.append(_card(r, facts, contact, names, template))

    if sort == "price_asc":
        cards.sort(key=lambda c: (c["facts"] or {}).get("rent_ils") or 10**9)
    elif sort == "price_desc":
        cards.sort(key=lambda c: (c["facts"] or {}).get("rent_ils") or -1, reverse=True)
    else:
        cards.sort(key=lambda c: (c["posted_at"] or c["first_seen_at"] or "", c["post_id"]), reverse=True)
    page = cards[offset:offset + limit]
    has_prices = bool(db.execute("SELECT 1 FROM sqlite_master WHERE name = 'listing_posts'").fetchone())
    for c in page:   # only for the cards actually returned
        c["photos"] = photos(db, c["post_id"])
        c["price_history"] = prices.history(db, c["post_id"]) if has_prices else []
    return {"total": len(cards), "items": page}


def location_info(facts, my_location=None):
    """The place of a listing, as precise as the post allows: {"text", "hints", "mine", "ai_text"}.

    text: street, neighborhood, city (what the AI read) - or the location I typed, which wins.
    hints: "near ..." details from the post, shown under it. ai_text is kept so I can switch back.
    """
    facts = facts or {}
    parts = [facts.get(k) for k in ("street", "neighborhood", "city")]
    ai_text = ", ".join(p for p in parts if p) or facts.get("location_summary_en") or None
    hints = [h for h in (facts.get("location_hints") or []) if h]
    return {"text": my_location or ai_text, "hints": hints[:3], "mine": bool(my_location), "ai_text": ai_text}


def _matches_area(facts, area, my_location=None):
    if not facts and not my_location:
        return False
    facts = facts or {}
    hay = " ".join(str(facts.get(k) or "") for k in ("city", "neighborhood", "street", "location_summary_en"))
    hay += " " + (my_location or "")
    hay += " " + " ".join(facts.get("location_hints") or [])
    return area.strip().lower() in hay.lower()


def _card(r, facts, contact, names, template):
    group = names.get(r["group_id"], r["group_id"])
    message = outreach.render_template(template, outreach.template_values(r, facts, group))
    return {
        "post_id": r["post_id"],
        "url": r["url"],
        "text": r["text"],
        "text_truncated": bool(r["text_truncated"]),
        "posted_at": r["posted_at"],
        "first_seen_at": r["first_seen_at"],
        "group_id": r["group_id"],
        "group_name": group,
        "author": r["author"],
        "contact": contact,
        "action": outreach.contact_action(contact, message),
        "ai": None if not r["final_decision"] else {
            "decision": r["final_decision"], "reason": r["final_reason"], "rule": r["final_rule"],
            "decided_by": r["decided_by"], "analyzed_at": r["analyzed_at"]},
        "facts": {k: facts.get(k) for k in CARD_FACTS} if facts else None,
        "location": location_info(facts, r["my_location"]),
        "price": {"rent": (facts or {}).get("rent_ils"), "total": (facts or {}).get("total_monthly_ils_known"),
                  "mine": bool((facts or {}).get("_rent_mine")), "ai_rent": (facts or {}).get(
                      "_ai_rent_ils" if (facts or {}).get("_rent_mine") else "rent_ils")},
        "partial_facts": bool(facts and facts.get("_partial")),
        "status": None if not r["status"] else {
            "status": r["status"], "note": r["status_note"], "source": r["status_source"],
            "updated_at": r["status_updated_at"]},
        "notes_count": r["notes_count"],
        "edited": r["edits"] > 0,
    }


def post_detail(db, cfg, user_id, post_id):
    """One card plus full facts, photos, notes and status history; None if unknown.

    Opening a post marks it as seen (used later for "post edited" alerts).
    """
    found = query_posts(db, cfg, user_id, post_ids=[post_id], limit=1)["items"]
    if not found:
        return None
    card = found[0]
    latest = db.execute("SELECT facts_json, hard_rules_json FROM post_analyses WHERE post_id = ? AND user_id = ?"
                        " AND status = 'ok' ORDER BY id DESC LIMIT 1", (post_id, user_id)).fetchone()
    card["full_facts"] = json.loads(latest["facts_json"]) if latest and latest["facts_json"] else None
    card["checks"] = json.loads(latest["hard_rules_json"]) if latest and latest["hard_rules_json"] else []
    card["photos"] = photos(db, post_id)
    card["notes"] = [dict(n) for n in db.execute(
        "SELECT id, body, created_at, updated_at FROM post_notes WHERE post_id = ? AND user_id = ? ORDER BY id",
        (post_id, user_id))]
    card["history"] = statuses.history(db, user_id, post_id)
    mark_seen(db, user_id, post_id)
    return card


def photos(db, post_id):
    """Photo sources: the downloaded copy when there is one, else Facebook's (expiring) link."""
    rows = db.execute("SELECT idx, url, local_path, more_hidden FROM post_images WHERE post_id = ? ORDER BY idx",
                      (post_id,)).fetchall()
    return {
        "items": [{"idx": p["idx"], "src": f"/api/photos/{post_id}/{p['idx']}" if p["local_path"] else p["url"],
                   "local": bool(p["local_path"])} for p in rows],
        "more_on_facebook": rows[0]["more_hidden"] if rows else 0,
    }


def mark_seen(db, user_id, post_id):
    now = statuses.now_iso()
    with db:
        db.execute("INSERT INTO post_seen (post_id, user_id, first_seen_at, last_seen_at) VALUES (?,?,?,?)"
                   " ON CONFLICT(post_id, user_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
                   (post_id, user_id, now, now))
