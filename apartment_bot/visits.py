"""Apartment visits: a checklist (items editable in Settings), notes, my own photos, a 1-5 rating,
and a comparison of the visited apartments.

My photos are saved under uploads/visits/<post id>/ on the PC (git-ignored), never sent anywhere.
"""
import json
import secrets
from pathlib import Path

from .statuses import now_iso

PROJECT_DIR = Path(__file__).resolve().parent.parent
UPLOADS = PROJECT_DIR / "uploads" / "visits"
VISIT_STATUSES = ("meeting_scheduled", "visited", "top_candidate")   # "meeting scheduled or later"
PHOTO_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/heic": ".heic",
               "image/heif": ".heif"}
MAX_PHOTO_BYTES = 15 * 1024 * 1024


def items(db, include_inactive=False):
    sql = "SELECT id, label, sort_order, active FROM visit_items"
    if not include_inactive:
        sql += " WHERE active = 1"
    return [dict(r) for r in db.execute(sql + " ORDER BY sort_order, id")]


def save_items(db, labels):
    """Set the default checklist to `labels` in this order (old items are hidden, not deleted)."""
    labels = [l.strip() for l in labels if l and l.strip()]
    current = {r["label"]: r["id"] for r in items(db, include_inactive=True)}
    with db:
        db.execute("UPDATE visit_items SET active = 0")
        for i, label in enumerate(labels):
            if label in current:
                db.execute("UPDATE visit_items SET active = 1, sort_order = ? WHERE id = ?", ((i + 1) * 10, current[label]))
            else:
                db.execute("INSERT INTO visit_items (label, sort_order) VALUES (?, ?)", (label, (i + 1) * 10))
    return items(db)


def get(db, user_id, post_id):
    """The visit of one post: rating, notes, checklist (active items + any answered ones), photos."""
    v = db.execute("SELECT rating, notes, visited_at, updated_at FROM visits WHERE post_id = ? AND user_id = ?",
                   (post_id, user_id)).fetchone()
    checks = {r["item_id"]: dict(r) for r in db.execute(
        "SELECT item_id, state, note FROM visit_checks WHERE post_id = ? AND user_id = ?", (post_id, user_id))}
    checklist = [{"item_id": it["id"], "label": it["label"], "state": checks.get(it["id"], {}).get("state"),
                  "note": checks.get(it["id"], {}).get("note")}
                 for it in items(db, include_inactive=True) if it["active"] or it["id"] in checks]
    photos = [{"id": r["id"], "src": f"/api/visit-photos/{r['id']}", "created_at": r["created_at"]}
              for r in db.execute("SELECT id, created_at FROM visit_photos WHERE post_id = ? AND user_id = ? ORDER BY id",
                                  (post_id, user_id))]
    return {"rating": v["rating"] if v else None, "notes": v["notes"] if v else None,
            "visited_at": v["visited_at"] if v else None, "checklist": checklist, "photos": photos}


def save(db, user_id, post_id, rating=None, notes=None, visited_at=None, checks=()):
    """Store rating / notes / checklist answers. checks: [{item_id, state: ok|issue|na|None, note}]."""
    if rating is not None and not 1 <= int(rating) <= 5:
        raise ValueError("rating must be 1-5")
    if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone():
        raise ValueError("unknown post")
    known = {it["id"] for it in items(db, include_inactive=True)}
    with db:
        db.execute("INSERT INTO visits (post_id, user_id, rating, notes, visited_at, updated_at) VALUES (?,?,?,?,?,?)"
                   " ON CONFLICT(post_id, user_id) DO UPDATE SET rating = excluded.rating, notes = excluded.notes,"
                   " visited_at = coalesce(excluded.visited_at, visits.visited_at), updated_at = excluded.updated_at",
                   (post_id, user_id, rating, notes, visited_at, now_iso()))
        for c in checks:
            if c["item_id"] not in known:
                raise ValueError(f"unknown checklist item {c['item_id']}")
            if c.get("state") not in (None, "ok", "issue", "na"):
                raise ValueError("state must be ok, issue or na")
            db.execute("INSERT OR REPLACE INTO visit_checks (post_id, user_id, item_id, state, note) VALUES (?,?,?,?,?)",
                       (post_id, user_id, c["item_id"], c.get("state"), c.get("note")))
    return get(db, user_id, post_id)


def add_photo(db, user_id, post_id, data, content_type):
    """Save one photo uploaded from the phone. Returns its id."""
    ext = PHOTO_TYPES.get((content_type or "").split(";")[0].strip().lower())
    if not ext:
        raise ValueError("only photos (JPEG, PNG, WebP, HEIC) can be uploaded")
    if not data or len(data) > MAX_PHOTO_BYTES:
        raise ValueError("photo is empty or larger than 15 MB")
    if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone():
        raise ValueError("unknown post")
    folder = UPLOADS / post_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{now_iso()[:10]}_{secrets.token_hex(4)}{ext}"
    path.write_bytes(data)
    with db:
        cur = db.execute("INSERT INTO visit_photos (post_id, user_id, path, created_at) VALUES (?,?,?,?)",
                         (post_id, user_id, path.relative_to(PROJECT_DIR).as_posix(), now_iso()))
    return cur.lastrowid


def photo_path(db, user_id, photo_id):
    r = db.execute("SELECT path FROM visit_photos WHERE id = ? AND user_id = ?", (photo_id, user_id)).fetchone()
    if not r:
        return None
    path = (PROJECT_DIR / r[0]).resolve()
    return path if UPLOADS.resolve() in path.parents and path.is_file() else None


def delete_photo(db, user_id, photo_id):
    path = photo_path(db, user_id, photo_id)
    with db:
        n = db.execute("DELETE FROM visit_photos WHERE id = ? AND user_id = ?", (photo_id, user_id)).rowcount
    if path:
        path.unlink(missing_ok=True)
    return bool(n)


def comparison(db, user_id):
    """Visited / scheduled apartments side by side: status, rating, issues, rent, area."""
    rows = db.execute(
        f"SELECT p.post_id, s.status, v.rating, v.notes, v.visited_at, a.facts_json"
        f" FROM posts p"
        f" LEFT JOIN post_status s ON s.post_id = p.post_id AND s.user_id = :u"
        f" LEFT JOIN visits v ON v.post_id = p.post_id AND v.user_id = :u"
        f" LEFT JOIN post_analyses a ON a.id = (SELECT max(b.id) FROM post_analyses b WHERE b.post_id = p.post_id"
        f"   AND b.user_id = :u AND b.status = 'ok')"
        f" WHERE s.status IN ({','.join(repr(x) for x in VISIT_STATUSES)}) OR v.post_id IS NOT NULL",
        {"u": user_id}).fetchall()
    out = []
    for r in rows:
        facts = json.loads(r["facts_json"]) if r["facts_json"] else {}
        checks = db.execute("SELECT i.label, c.state, c.note FROM visit_checks c JOIN visit_items i ON i.id = c.item_id"
                            " WHERE c.post_id = ? AND c.user_id = ? AND c.state IS NOT NULL ORDER BY i.sort_order",
                            (r["post_id"], user_id)).fetchall()
        out.append({
            "post_id": r["post_id"], "status": r["status"], "rating": r["rating"], "notes": r["notes"],
            "visited_at": r["visited_at"],
            "rent_ils": facts.get("rent_ils"), "total_monthly_ils_known": facts.get("total_monthly_ils_known"),
            "area": ", ".join(x for x in (facts.get("street"), facts.get("neighborhood"), facts.get("city")) if x) or None,
            "rooms": facts.get("rooms"),
            "issues": [{"label": c["label"], "note": c["note"]} for c in checks if c["state"] == "issue"],
            "ok": [c["label"] for c in checks if c["state"] == "ok"],
            "photos": db.execute("SELECT count(*) FROM visit_photos WHERE post_id = ? AND user_id = ?",
                                 (r["post_id"], user_id)).fetchone()[0],
        })
    return sorted(out, key=lambda x: (-(x["rating"] or 0), x["rent_ils"] or 10**9))
