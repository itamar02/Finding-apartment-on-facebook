"""Edited posts. `posts` keeps the text as first seen; later changes go to `post_versions`.

The scanner calls record_if_changed for every post it reads again. A real edit is stored as
kind "edit" (re-analyzed, and announced if I had seen the post); a text that was cut short
before and is now read in full is stored as kind "expanded" (re-analyzed, not announced).
"""
import difflib
from datetime import datetime

from .textnorm import normalize as _norm   # emoji and spacing never count as an edit


def current_text(db, post_id):
    """(text, truncated) currently stored for a post: its latest version, else the original."""
    v = db.execute("SELECT text, text_truncated FROM post_versions WHERE post_id = ? ORDER BY id DESC LIMIT 1",
                   (post_id,)).fetchone()
    if v:
        return v[0], bool(v[1])
    p = db.execute("SELECT text, text_truncated FROM posts WHERE post_id = ?", (post_id,)).fetchone()
    return (p[0], bool(p[1])) if p else (None, False)


def record_if_changed(db, post_id, text, truncated, now=None):
    """Store a new version when the text really changed. Returns "edit", "expanded" or None.

    No transaction of its own (the scanner wraps a whole group in one).
    """
    if not _norm(text):
        return None
    old, old_truncated = current_text(db, post_id)
    if old is None or _norm(old) == _norm(text):
        return None
    if truncated and not old_truncated:
        return None                     # read less of it this time; nothing changed
    kind = "edit"
    if old_truncated and not truncated and _norm(text).startswith(_norm(old)[:max(20, len(_norm(old)) - 15)]):
        kind = "expanded"
    db.execute("INSERT INTO post_versions (post_id, text, text_truncated, kind, seen_at) VALUES (?,?,?,?,?)",
               (post_id, text, int(bool(truncated)), kind,
                (now or datetime.now().astimezone()).isoformat(timespec="seconds")))
    return kind


def change_summary(old, new, limit=160):
    """A short "added / removed" description of an edit, for the notification."""
    a, b = _norm(old).split(" "), _norm(new).split(" ")
    added, removed = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            removed.append(" ".join(a[i1:i2]))
        if op in ("replace", "insert"):
            added.append(" ".join(b[j1:j2]))
    parts = []
    if added:
        parts.append("נוסף: " + " … ".join(added))
    if removed:
        parts.append("הוסר: " + " … ".join(removed))
    text = " | ".join(parts) or "הנוסח השתנה"
    return text if len(text) <= limit else text[:limit - 1] + "…"


def previous_text(db, version_id):
    """The text a version replaced."""
    v = db.execute("SELECT post_id FROM post_versions WHERE id = ?", (version_id,)).fetchone()
    prev = db.execute("SELECT text FROM post_versions WHERE post_id = ? AND id < ? ORDER BY id DESC LIMIT 1",
                      (v[0], version_id)).fetchone()
    if prev:
        return prev[0]
    return db.execute("SELECT text FROM posts WHERE post_id = ?", (v[0],)).fetchone()[0]
