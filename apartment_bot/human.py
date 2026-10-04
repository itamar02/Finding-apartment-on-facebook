"""Decisions made by the human: listings rejected by hand ("human rejected" pile).

Stored in `human_decisions`, separate from the AI analyses, so re-running the analysis
never touches them. A human rejection wins over every automatic decision.
"""
import re
from datetime import datetime


def parse_post_id(text):
    """Facebook post ID from a bare ID or a post URL (digits at the end of the path)."""
    m = re.search(r"(\d{6,})/?(?:\?.*)?$", text.strip())
    return m.group(1) if m else None


def reject(db, user_id, post_ids, note=None):
    """Mark listings as rejected by the human. Returns (rejected ids, unknown ids)."""
    done, unknown = [], []
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with db:
        for pid in post_ids:
            if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (pid,)).fetchone():
                unknown.append(pid)
                continue
            db.execute("INSERT OR REPLACE INTO human_decisions (post_id, user_id, decision, note, decided_at)"
                       " VALUES (?, ?, 'REJECT', ?, ?)", (pid, user_id, note, now))
            done.append(pid)
    return done, unknown


def undo(db, user_id, post_ids):
    """Remove human rejections; the listings return to their automatic decision. Returns removed count."""
    with db:
        return sum(db.execute("DELETE FROM human_decisions WHERE post_id = ? AND user_id = ?",
                              (pid, user_id)).rowcount for pid in post_ids)


def rejected(db, user_id):
    """{post_id: (note, decided_at)} of everything the human rejected."""
    return {r["post_id"]: (r["note"], r["decided_at"]) for r in db.execute(
        "SELECT post_id, note, decided_at FROM human_decisions WHERE user_id = ? AND decision = 'REJECT'", (user_id,))}
