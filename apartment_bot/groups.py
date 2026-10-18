"""The Facebook groups to scan, stored in `fb_groups`.

config.yaml's `facebook.groups` seeds the table the first time (all selected), so older
setups keep working. After that the table is the source of truth: the app's groups screen
ticks groups on and off, and discover_groups.py adds groups it finds (not selected).
"""
import re

from .statuses import now_iso

_GROUP_URL = re.compile(r"facebook\.com/groups/([^/?#]+)", re.I)


def parse_group_id(text):
    """Group id from a group URL or a bare id/name; None if it doesn't look like one."""
    text = (text or "").strip()
    m = _GROUP_URL.search(text)
    gid = m.group(1) if m else text
    return gid if re.fullmatch(r"[A-Za-z0-9._-]{2,100}", gid or "") else None


def group_url(gid):
    return f"https://www.facebook.com/groups/{gid}/"


def seed_from_config(db, cfg):
    """Fill an empty fb_groups table from config.yaml (selected). Returns how many were added."""
    if db.execute("SELECT 1 FROM fb_groups LIMIT 1").fetchone():
        return 0
    now = now_iso()
    rows = [(g["id"], g.get("name"), group_url(g["id"]), 1, now, now) for g in cfg["facebook"].get("groups") or []]
    with db:
        db.executemany("INSERT OR IGNORE INTO fb_groups (id, name, url, is_member, selected, source, discovered_at,"
                       " updated_at) VALUES (?, ?, ?, ?, 1, 'config', ?, ?)", rows)
    return len(rows)


def list_groups(db):
    """Every known group: selected first, then by relevance and name."""
    return [dict(r) for r in db.execute(
        "SELECT * FROM fb_groups ORDER BY selected DESC, coalesce(relevance_score, -1) DESC,"
        " coalesce(member_count, 0) DESC, name")]


def selected_ids(db):
    return [r[0] for r in db.execute("SELECT id FROM fb_groups WHERE selected = 1 ORDER BY name, id")]


def names(db, cfg=None):
    """{group id: display name} from the table, falling back to config.yaml."""
    out = {g["id"]: g.get("name") or g["id"] for g in (cfg or {}).get("facebook", {}).get("groups") or []}
    for gid, name in db.execute("SELECT id, name FROM fb_groups"):
        out[gid] = name or out.get(gid) or gid
    return out


def set_selected(db, gid, selected):
    with db:
        # Choosing a group means I'm in it now (the "not a member" flag may be from before I joined).
        n = db.execute("UPDATE fb_groups SET selected = ?, is_member = CASE WHEN ? THEN 1 ELSE is_member END,"
                       " updated_at = ? WHERE id = ?", (int(bool(selected)), int(bool(selected)), now_iso(), gid)).rowcount
    return bool(n)


def add_manual(db, text, name=None, selected=True):
    """Add a group by URL or id (from the app). Returns its id; raises ValueError if unusable."""
    gid = parse_group_id(text)
    if not gid:
        raise ValueError("not a Facebook group link or id")
    now = now_iso()
    with db:
        db.execute("INSERT INTO fb_groups (id, name, url, selected, source, discovered_at, updated_at)"
                   " VALUES (?, ?, ?, ?, 'manual', ?, ?)"
                   " ON CONFLICT(id) DO UPDATE SET selected = excluded.selected, updated_at = excluded.updated_at,"
                   " name = coalesce(excluded.name, fb_groups.name)",
                   (gid, name, group_url(gid), int(bool(selected)), now, now))
    return gid


def upsert_discovered(db, found, source, query=None):
    """Store groups found by discover_groups.py. Known groups keep their selection.

    found: [{id, name, member_count, privacy, is_member, description}] (missing = unknown).
    Returns (added, updated).
    """
    added = updated = 0
    now = now_iso()
    with db:
        for g in found:
            gid = parse_group_id(g.get("id"))
            if not gid:
                continue
            exists = db.execute("SELECT 1 FROM fb_groups WHERE id = ?", (gid,)).fetchone()
            if exists:
                db.execute(
                    "UPDATE fb_groups SET name = coalesce(?, name), member_count = coalesce(?, member_count),"
                    " privacy = coalesce(?, privacy), is_member = coalesce(?, is_member),"
                    " description = coalesce(?, description), updated_at = ? WHERE id = ?",
                    (g.get("name"), g.get("member_count"), g.get("privacy"), g.get("is_member"),
                     g.get("description"), now, gid))
                updated += 1
            else:
                db.execute(
                    "INSERT INTO fb_groups (id, name, url, member_count, privacy, is_member, description, selected,"
                    " source, search_query, discovered_at, updated_at) VALUES (?,?,?,?,?,?,?,0,?,?,?,?)",
                    (gid, g.get("name"), group_url(gid), g.get("member_count"), g.get("privacy"),
                     g.get("is_member"), g.get("description"), source, query, now, now))
                added += 1
    return added, updated


def set_relevance(db, gid, score, reason):
    with db:
        db.execute("UPDATE fb_groups SET relevance_score = ?, relevance_reason = ?, updated_at = ? WHERE id = ?",
                   (score, reason, now_iso(), gid))
