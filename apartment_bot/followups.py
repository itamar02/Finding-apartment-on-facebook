"""Follow-up reminders: a post that sits too long in Contacted / Awaiting reply / Needs details.

The hours per status are a setting (followup_hours). A reminder belongs to one "status period"
(the post_status.updated_at it started from), so changing the status starts a fresh clock,
and snoozing or dismissing only affects the current period.
"""
from datetime import datetime, timedelta

from . import settings


def _parse(iso):
    return datetime.fromisoformat(iso)


def due(db, user_id, now=None):
    """Posts whose status is older than its threshold and not snoozed or dismissed.

    Returns [{post_id, status, note, since, overdue_hours}] oldest first.
    """
    now = now or datetime.now().astimezone()
    hours = settings.get(db, "followup_hours") or {}
    if not hours:
        return []
    rows = db.execute(
        f"SELECT s.post_id, s.status, s.note, s.updated_at, f.snoozed_until, f.dismissed"
        f" FROM post_status s LEFT JOIN followups f ON f.post_id = s.post_id AND f.user_id = s.user_id"
        f"   AND f.status_since = s.updated_at || '|' || s.status"
        f" WHERE s.user_id = ? AND s.status IN ({','.join('?' * len(hours))})",
        [user_id, *hours]).fetchall()
    out = []
    for r in rows:
        limit = hours.get(r["status"])
        if not limit or r["dismissed"]:
            continue
        if r["snoozed_until"] and _parse(r["snoozed_until"]) > now:
            continue
        due_at = _parse(r["updated_at"]) + timedelta(hours=float(limit))
        if due_at <= now:
            out.append({"post_id": r["post_id"], "status": r["status"], "note": r["note"], "since": r["updated_at"],
                        "snoozed_until": r["snoozed_until"],
                        "overdue_hours": round((now - due_at).total_seconds() / 3600, 1)})
    return sorted(out, key=lambda d: d["since"])


def _upsert(db, user_id, post_id, **values):
    row = db.execute("SELECT updated_at || '|' || status FROM post_status WHERE post_id = ? AND user_id = ?",
                     (post_id, user_id)).fetchone()
    if not row:
        raise ValueError("this post has no status")
    with db:
        db.execute("INSERT OR IGNORE INTO followups (post_id, user_id, status_since) VALUES (?,?,?)",
                   (post_id, user_id, row[0]))
        for col, val in values.items():
            db.execute(f"UPDATE followups SET {col} = ? WHERE post_id = ? AND user_id = ? AND status_since = ?",
                       (val, post_id, user_id, row[0]))


def snooze(db, user_id, post_id, hours, now=None):
    until = (now or datetime.now().astimezone()) + timedelta(hours=hours)
    _upsert(db, user_id, post_id, snoozed_until=until.isoformat(timespec="seconds"))


def dismiss(db, user_id, post_id):
    _upsert(db, user_id, post_id, dismissed=1)

