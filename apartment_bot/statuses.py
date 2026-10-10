"""My pipeline status per post (new, interested, contacted, ...), separate from the AI verdict.

The AI verdict is always derived from `post_analyses` and never copied here. This module
owns `post_status` (the current status), `post_status_history` (append-only) and the
editable `statuses` list.

"not_relevant" is the same thing as a hand rejection (reject.py): setting it also writes
`human_decisions`, and leaving it removes that row, so the CLI scripts, the exports and the
AI's skip list always agree with the app.
"""
from datetime import datetime

NOT_RELEVANT = "not_relevant"
SOURCES = ("human", "gpt", "system")


class StatusError(ValueError):
    pass


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def list_statuses(db):
    """All statuses in display order, as dicts."""
    return [dict(r) for r in db.execute("SELECT * FROM statuses ORDER BY sort_order, key")]


def status_keys(db):
    return {r[0] for r in db.execute("SELECT key FROM statuses")}


def current(db, user_id, post_id):
    """The current status row as a dict, or None."""
    r = db.execute("SELECT * FROM post_status WHERE post_id = ? AND user_id = ?", (post_id, user_id)).fetchone()
    return dict(r) if r else None


def history(db, user_id, post_id):
    """Every change for a post, oldest first."""
    return [dict(r) for r in db.execute(
        "SELECT * FROM post_status_history WHERE post_id = ? AND user_id = ? ORDER BY id", (post_id, user_id))]


def record(db, user_id, post_id, status, note=None, source="human", when=None):
    """Write the current status and a history row. No transaction of its own; status None clears.

    Does not touch human_decisions - callers that need the sync use set_status.
    """
    if source not in SOURCES:
        raise StatusError(f"unknown source {source!r}")
    if status is not None and status not in status_keys(db):
        raise StatusError(f"unknown status {status!r}")
    when = when or now_iso()
    old = current(db, user_id, post_id)
    db.execute(
        "INSERT INTO post_status_history (post_id, user_id, status, note, source, previous_status,"
        " previous_note, changed_at) VALUES (?,?,?,?,?,?,?,?)",
        (post_id, user_id, status, note, source, old and old["status"], old and old["note"], when))
    if status is None:
        db.execute("DELETE FROM post_status WHERE post_id = ? AND user_id = ?", (post_id, user_id))
    else:
        db.execute("INSERT OR REPLACE INTO post_status (post_id, user_id, status, note, source, updated_at)"
                   " VALUES (?,?,?,?,?,?)", (post_id, user_id, status, note, source, when))
    return old


def set_status(db, user_id, post_id, status, note=None, source="human"):
    """Change a post's status (None clears it) and keep human_decisions in sync.

    Returns (previous status row or None, new status row or None).
    """
    if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone():
        raise StatusError(f"unknown post {post_id}")
    when = now_iso()
    with db:
        old = record(db, user_id, post_id, status, note, source, when)
        _sync_human(db, user_id, post_id, old and old["status"], status, note, when)
    return old, current(db, user_id, post_id)


def undo(db, user_id, post_id):
    """Return a post to the status it had before its last change. Returns the new row or None."""
    last = db.execute("SELECT * FROM post_status_history WHERE post_id = ? AND user_id = ?"
                      " ORDER BY id DESC LIMIT 1", (post_id, user_id)).fetchone()
    if not last:
        raise StatusError("nothing to undo")
    return set_status(db, user_id, post_id, last["previous_status"], last["previous_note"], "human")[1]


def _sync_human(db, user_id, post_id, old_status, new_status, note, when):
    """not_relevant <-> human_decisions REJECT."""
    if new_status == NOT_RELEVANT:
        db.execute("INSERT OR REPLACE INTO human_decisions (post_id, user_id, decision, note, decided_at)"
                   " VALUES (?, ?, 'REJECT', ?, ?)", (post_id, user_id, note, when))
    elif old_status == NOT_RELEVANT:
        db.execute("DELETE FROM human_decisions WHERE post_id = ? AND user_id = ?", (post_id, user_id))


# --- editing the status list (Settings) ---------------------------------------------------

def save_status(db, key, label, color, needs_note=False, sort_order=None):
    """Add a status or rename / recolor an existing one. Keys are stable ids."""
    key = (key or "").strip().lower().replace(" ", "_")
    if not key or not key.replace("_", "").isalnum():
        raise StatusError("a status key needs letters, digits or _")
    if not (label or "").strip():
        raise StatusError("a status needs a label")
    with db:
        if db.execute("SELECT 1 FROM statuses WHERE key = ?", (key,)).fetchone():
            db.execute("UPDATE statuses SET label = ?, color = ?, needs_note = ?"
                       + (", sort_order = ?" if sort_order is not None else "") + " WHERE key = ?",
                       (label.strip(), color, int(bool(needs_note)),
                        *([sort_order] if sort_order is not None else []), key))
        else:
            if sort_order is None:
                sort_order = (db.execute("SELECT max(sort_order) FROM statuses").fetchone()[0] or 0) + 10
            db.execute("INSERT INTO statuses (key, label, color, sort_order, needs_note) VALUES (?,?,?,?,?)",
                       (key, label.strip(), color, sort_order, int(bool(needs_note))))
    return key


def reorder(db, keys):
    """Set the display order to the given list of keys (must be every key exactly once)."""
    if sorted(keys) != sorted(status_keys(db)):
        raise StatusError("reorder needs every status key exactly once")
    with db:
        for i, k in enumerate(keys):
            db.execute("UPDATE statuses SET sort_order = ? WHERE key = ?", ((i + 1) * 10, k))


def delete_status(db, key):
    """Delete a status that is not built in and not in use."""
    row = db.execute("SELECT built_in FROM statuses WHERE key = ?", (key,)).fetchone()
    if not row:
        raise StatusError(f"unknown status {key!r}")
    if row[0]:
        raise StatusError("built-in statuses can be renamed but not deleted")
    if db.execute("SELECT 1 FROM post_status WHERE status = ? LIMIT 1", (key,)).fetchone():
        raise StatusError("this status is still used by some posts")
    with db:
        db.execute("DELETE FROM statuses WHERE key = ?", (key,))
