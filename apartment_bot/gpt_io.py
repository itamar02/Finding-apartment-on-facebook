"""Hand listings to an external AI (ChatGPT or any chat model) and take its statuses back.

Export: every recent listing that has no status set by me, with its text, facts, AI verdict
and contacts, plus a ready prompt that asks for a JSON list of {post_id, status, note}.
Import: the reply is parsed, checked (known post, allowed status), shown as a preview, and
applied only after confirmation, with source "gpt". A status I set myself is never
overwritten unless I ask for it.
"""
import json
import re

from . import feed, settings, statuses

PROMPT = """You help me sort apartment listings from Facebook rental groups in Israel.
Below is a JSON list of listings. For each one, choose the status that fits best from this list
(use the key, not the label):

{status_list}

My requirements, in short:
{requirements}

Rules:
- Return ONLY a JSON array, no other text: [{{"post_id": "...", "status": "<key>", "note": "..."}}]
- One entry per listing you are confident about; leave out listings you can't judge.
- "note" is one short sentence in Hebrew: why, or what I should ask the poster.
- Use only the keys above, and copy post_id exactly.

Listings:
"""


def _requirements(user):
    b, l, a = user["budget"], user["listing"], user["areas"]
    lines = [
        f"- Budget: up to {b['preferred_max_rent']} ILS preferred, {b['absolute_max_rent']} ILS at most",
        f"- Listing types: {', '.join(l['allowed_types'])}; cities: {', '.join(l['search_cities'])}",
    ]
    if a["preferred"]:
        lines.append(f"- Preferred areas: {', '.join(a['preferred'])}")
    if a["excluded"]:
        lines.append(f"- Excluded areas: {', '.join(a['excluded'])}")
    f = user["features"]
    if f["required"]:
        lines.append(f"- Must have: {', '.join(f['required'])}")
    if f["dealbreakers"]:
        lines.append(f"- Dealbreakers: {', '.join(f['dealbreakers'])}")
    if user.get("free_text"):
        lines.append(f"- Also: {user['free_text']}")
    return "\n".join(lines)


def export(db, cfg, user, include_old=False):
    """{"prompt": text, "posts": [...], "count": n} for listings without a status set by me."""
    cards = feed.query_posts(db, cfg, user["id"], include_old=include_old, limit=10**6)["items"]
    posts = []
    for c in cards:
        if c["status"] and c["status"]["source"] == "human":
            continue
        if c["ai"] and c["ai"]["decision"] == "REJECT":
            continue   # rejected automatically: not worth the external AI's time
        posts.append({
            "post_id": c["post_id"],
            "posted_at": c["posted_at"],
            "group": c["group_name"],
            "text": c["text"],
            "facts": c["facts"],
            "ai_verdict": c["ai"] and {"decision": c["ai"]["decision"], "reason": c["ai"]["reason"]},
            "contacts": {"phones": c["contact"]["phones"], "whatsapp": c["contact"]["whatsapp"]},
        })
    status_list = "\n".join(f"- {s['key']}: {s['label']}" for s in statuses.list_statuses(db))
    prompt = PROMPT.format(status_list=status_list, requirements=_requirements(user))
    return {"prompt": prompt, "posts": posts, "count": len(posts),
            "recent_days": None if include_old else settings.recent_days(db, cfg)}


def as_text(payload):
    """The prompt and the listings in one block, ready to paste into a chat."""
    return payload["prompt"] + json.dumps(payload["posts"], ensure_ascii=False, indent=1)


def parse_reply(text):
    """The JSON array from a model reply (code fences and chatter around it are ignored)."""
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < start:
        raise ValueError("no JSON list found in the reply")
    data = json.loads(text[start:end + 1])
    if not isinstance(data, list):
        raise ValueError("the reply is not a JSON list")
    return data


def preview(db, user_id, text):
    """Check a reply and show what would change.

    Returns {"changes": [...], "unchanged": n, "conflicts": [...], "errors": [...]}. Each change
    is {post_id, status, note, current: {status, source, note} | None}. Conflicts are entries
    that would replace a status I set myself (applied only with override_human).
    """
    try:
        items = parse_reply(text)
    except ValueError as e:
        return {"changes": [], "unchanged": 0, "conflicts": [], "errors": [str(e)]}
    keys = statuses.status_keys(db)
    labels = {s["key"]: s["label"] for s in statuses.list_statuses(db)}
    by_label = {v: k for k, v in labels.items()}
    out = {"changes": [], "unchanged": 0, "conflicts": [], "errors": []}
    seen = set()
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            out["errors"].append(f"item {i}: not an object")
            continue
        pid = str(item.get("post_id") or "").strip()
        status = str(item.get("status") or "").strip()
        status = status if status in keys else by_label.get(status, status)   # a label instead of a key is fine
        note = item.get("note")
        note = str(note).strip() if note not in (None, "") else None
        if not pid:
            out["errors"].append(f"item {i}: no post_id")
            continue
        if pid in seen:
            out["errors"].append(f"{pid}: listed twice; the first one counts")
            continue
        seen.add(pid)
        if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (pid,)).fetchone():
            out["errors"].append(f"{pid}: unknown post")
            continue
        if status not in keys:
            out["errors"].append(f"{pid}: unknown status {status!r}")
            continue
        cur = statuses.current(db, user_id, pid)
        if cur and cur["status"] == status and (cur["note"] or None) == note:
            out["unchanged"] += 1
            continue
        change = {"post_id": pid, "status": status, "label": labels[status], "note": note,
                  "current": cur and {"status": cur["status"], "label": labels.get(cur["status"], cur["status"]),
                                      "source": cur["source"], "note": cur["note"]}}
        (out["conflicts"] if cur and cur["source"] == "human" else out["changes"]).append(change)
    return out


def apply(db, user_id, text, override_human=False):
    """Apply a checked reply with source "gpt". Returns {"applied": n, "skipped": n, "errors": [...]}."""
    result = preview(db, user_id, text)
    todo = result["changes"] + (result["conflicts"] if override_human else [])
    done = []
    for c in todo:
        statuses.set_status(db, user_id, c["post_id"], c["status"], c["note"], "gpt")
        done.append({"post_id": c["post_id"], "status": c["status"], "note": c["note"], "previous": c["current"]})
    with db:
        db.execute("INSERT INTO gpt_imports (created_at, items_json, applied) VALUES (?, ?, ?)",
                   (statuses.now_iso(), json.dumps(done, ensure_ascii=False), len(done)))
    return {"applied": len(done), "skipped": len(result["conflicts"]) if not override_human else 0,
            "errors": result["errors"]}
