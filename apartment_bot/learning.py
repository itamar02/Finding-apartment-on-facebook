"""Learning loop: suggest config.yaml changes from what I actually did.

Looks at the listings I marked "not relevant" (especially with a note), the ones I liked
(interested, contacted, visited, top candidate) and problems found on visits, and asks the
AI for concrete, small changes - e.g. "you rejected 8 ground-floor apartments; add
'ground floor' to dealbreakers?". The code keeps only changes to editable fields, with the
right type, backed by at least MIN_EVIDENCE of my listings. Nothing changes until I accept
a proposal in the app; accepted ones are written by config_edit (backup, comments kept).

Settings: "learning": {"mode": "off" | "weekly" | "on_demand", "last_run": iso}.
"""
import json
from datetime import datetime, timedelta

from . import config_edit, settings
from .db import EFFECTIVE_TEXT_SQL
from .statuses import now_iso

MIN_EVIDENCE = 3
LIKED = ("interested", "contacted", "awaiting_reply", "meeting_scheduled", "visited", "top_candidate")
FACT_KEYS = ("listing_type", "rent_ils", "total_monthly_ils_known", "city", "neighborhood", "street", "rooms",
             "size_sqm", "floor", "elevator", "balcony", "parking", "shelter", "furnished", "broker", "pets")

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["proposals"],
    "properties": {"proposals": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["path", "op", "value", "reason", "evidence_post_ids"],
        "properties": {
            "path": {"type": "string", "enum": sorted(config_edit.EDITABLE)},
            "op": {"type": "string", "enum": ["add", "remove", "set"]},
            "value": {"type": "string"},          # a list item, or the new value as text
            "reason": {"type": "string"},
            "evidence_post_ids": {"type": "array", "items": {"type": "string"}},
        }}}},
}

PROMPT = """I am searching for a rental apartment. Below are my current search settings and the
listings I reacted to. Suggest at most 5 small, concrete changes to my settings that would make
the automatic filter match my real choices better.

Rules:
- Only suggest a change that at least {min_ev} of the listings below support, and list their
  post_ids in evidence_post_ids.
- Prefer adding a dealbreaker / required feature / excluded area, or adjusting a number.
- op "add" / "remove" for list fields (value = one item, in the language my settings use);
  op "set" for single values (value as text, e.g. "5500").
- reason: one short Hebrew sentence that states the count, e.g. "דחית 8 דירות בקומת קרקע".
- Don't suggest something my settings already say. Return {{"proposals": []}} if nothing is clear.

My settings:
{settings}

Listings I marked NOT RELEVANT (with my note when I wrote one):
{rejected}

Listings I LIKED:
{liked}

Problems I noted on visits:
{issues}"""


def _facts(db, user_id, post_id):
    r = db.execute("SELECT facts_json FROM post_analyses WHERE post_id = ? AND user_id = ? AND status = 'ok'"
                   " ORDER BY id DESC LIMIT 1", (post_id, user_id)).fetchone()
    f = json.loads(r[0]) if r and r[0] else {}
    return {k: f.get(k) for k in FACT_KEYS if f.get(k) not in (None, "", "unknown")}


def gather(db, user_id, limit=60):
    """The listings and notes the AI sees: {"rejected": [...], "liked": [...], "issues": [...]}."""
    def rows(status_sql, params):
        out = []
        for r in db.execute(
                f"SELECT s.post_id, s.status, s.note, {EFFECTIVE_TEXT_SQL} AS text,"
                " (SELECT group_concat(n.body, ' | ') FROM post_notes n WHERE n.post_id = s.post_id"
                "  AND n.user_id = s.user_id) AS notes"
                f" FROM post_status s JOIN posts p ON p.post_id = s.post_id WHERE s.user_id = ? AND {status_sql}"
                " ORDER BY (s.note IS NOT NULL) DESC, s.updated_at DESC LIMIT ?", (user_id, *params, limit)):
            out.append({"post_id": r["post_id"], "my_note": " | ".join(x for x in (r["note"], r["notes"]) if x) or None,
                        "facts": _facts(db, user_id, r["post_id"]), "text": (r["text"] or "")[:400]})
        return out
    rejected = rows("s.status = 'not_relevant' AND s.source = 'human'", ())
    liked = rows(f"s.status IN ({','.join('?' * len(LIKED))})", LIKED)
    issues = [dict(r) for r in db.execute(
        "SELECT c.post_id, i.label, c.note FROM visit_checks c JOIN visit_items i ON i.id = c.item_id"
        " WHERE c.user_id = ? AND c.state = 'issue'", (user_id,))]
    return {"rejected": rejected, "liked": liked, "issues": issues}


def validate(db, user_id, cfg, proposal, data):
    """A proposal turned into (path, op, value, reason, evidence), or None if the code rejects it."""
    path, op, raw = proposal["path"], proposal["op"], (proposal["value"] or "").strip()
    if path not in config_edit.EDITABLE or not raw:
        return None
    kind = config_edit.EDITABLE[path]
    is_list = kind == config_edit.TEXT_LIST or (isinstance(kind, tuple) and kind[0] == "list")
    if is_list != (op in ("add", "remove")):
        return None
    known = {p["post_id"] for p in data["rejected"] + data["liked"]} | {i["post_id"] for i in data["issues"]}
    evidence = [e for e in dict.fromkeys(proposal["evidence_post_ids"]) if e in known]
    if len(evidence) < MIN_EVIDENCE:
        return None
    current = config_edit.read(cfg, user_id)[path]
    try:
        value = config_edit.check_value(path, raw) if op == "set" else raw
        if op != "set" and isinstance(kind, tuple):
            config_edit.check_value(path, [value])
    except config_edit.EditError:
        return None
    if (op == "add" and value in (current or [])) or (op == "remove" and value not in (current or [])) \
            or (op == "set" and value == current):
        return None
    return path, op, value, proposal["reason"].strip(), evidence


def run(db, cfg, user_id, router, now=None):
    """Ask for proposals and store the valid new ones. Returns how many were added."""
    data = gather(db, user_id)
    if len(data["rejected"]) + len(data["liked"]) < MIN_EVIDENCE:
        _mark_run(db, now)
        return 0
    current = {k: v for k, v in config_edit.read(cfg, user_id).items() if v not in (None, [], "")}
    prompt = PROMPT.format(min_ev=MIN_EVIDENCE, settings=json.dumps(current, ensure_ascii=False, indent=1),
                           rejected=json.dumps(data["rejected"], ensure_ascii=False),
                           liked=json.dumps(data["liked"], ensure_ascii=False),
                           issues=json.dumps(data["issues"], ensure_ascii=False) if data["issues"] else "none")
    raw, *_ = router.json_call("learning", [{"type": "text", "text": "You tune apartment-search settings. Answer in JSON."}],
                               prompt, SCHEMA)
    added = 0
    for p in json.loads(raw)["proposals"][:5]:
        v = validate(db, user_id, cfg, p, data)
        if not v:
            continue
        path, op, value, reason, evidence = v
        seen = db.execute("SELECT 1 FROM config_proposals WHERE user_id = ? AND path = ? AND op = ? AND value_json = ?"
                          " AND status IN ('open', 'declined')", (user_id, path, op, json.dumps(value, ensure_ascii=False))).fetchone()
        if seen:
            continue   # already waiting, or I said no before
        with db:
            db.execute("INSERT INTO config_proposals (user_id, path, op, value_json, reason, evidence, status, created_at)"
                       " VALUES (?,?,?,?,?,?, 'open', ?)",
                       (user_id, path, op, json.dumps(value, ensure_ascii=False), reason,
                        json.dumps(evidence), now_iso()))
        added += 1
    _mark_run(db, now)
    return added


def _mark_run(db, now=None):
    conf = settings.get(db, "learning")
    conf["last_run"] = (now or datetime.now().astimezone()).isoformat(timespec="seconds")
    settings.put(db, {"learning": conf})


def due(db, now=None):
    """True when the weekly learning run should happen."""
    conf = settings.get(db, "learning")
    if conf.get("mode") != "weekly":
        return False
    last = conf.get("last_run")
    now = now or datetime.now().astimezone()
    return not last or datetime.fromisoformat(last) <= now - timedelta(days=7)


def proposals(db, user_id, status="open"):
    return [{**dict(r), "value": json.loads(r["value_json"]), "evidence": json.loads(r["evidence"])}
            for r in db.execute("SELECT * FROM config_proposals WHERE user_id = ? AND status = ? ORDER BY id DESC",
                                (user_id, status))]


def decide(db, user_id, proposal_id, accept, config_path=None):
    """Accept (write config.yaml) or decline a proposal. Returns the new cfg when accepted, else None."""
    r = db.execute("SELECT * FROM config_proposals WHERE id = ? AND user_id = ? AND status = 'open'",
                   (proposal_id, user_id)).fetchone()
    if not r:
        raise config_edit.EditError("unknown or already decided proposal")
    new_cfg = None
    if accept:
        kwargs = {"path": config_path} if config_path else {}
        new_cfg, _backup = config_edit.apply([(r["path"], r["op"], json.loads(r["value_json"]))], user_id, **kwargs)
    with db:
        db.execute("UPDATE config_proposals SET status = ?, decided_at = ? WHERE id = ?",
                   ("accepted" if accept else "declined", now_iso(), proposal_id))
    return new_cfg
