"""From a WhatsApp conversation to a viewing in the calendar - only with my explicit confirmation.

1. I share (Android), paste or upload (an exported .txt) a WhatsApp chat into the app.
   No WhatsApp library is used; the app only reads what I hand it.
2. The chat is linked to a listing by a phone number that appears in both, or I pick one.
3. The AI proposes a meeting (date, time, address) ONLY if the chat shows that I explicitly
   agreed to a time, and quotes the lines that show it. The code checks that every quote is
   really in the chat; otherwise there is no proposal.
4. Nothing reaches the calendar until I tap Confirm (possibly after correcting the details).
   Then one Google Calendar event is created and the status becomes "Meeting scheduled".
"""
import json
import re
from datetime import date as date_cls, datetime, timedelta

from . import gcal, settings, statuses
from .contacts import phones_in_text
from .db import EFFECTIVE_TEXT_SQL

MAX_CHAT_CHARS = 20000          # the end of a long chat is what matters

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["agreed", "date", "time", "address", "evidence", "explanation"],
    "properties": {
        "agreed": {"type": "boolean"},
        "date": {"type": "string"},          # YYYY-MM-DD or ""
        "time": {"type": "string"},          # HH:MM or ""
        "address": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
    },
}

PROMPT = """This is a WhatsApp conversation between me (someone looking for an apartment to rent)
and a person offering an apartment. Today is {today}. {me}

Decide whether the conversation shows that I EXPLICITLY agreed to a specific date and time to come
and see the apartment. Only a clear agreement counts: a time was suggested and I accepted it (for
example "סגור", "מעולה, נתראה ב-18:00", "מתאים לי"), or I suggested a time and they accepted it and
I confirmed. A suggestion nobody accepted, a vague plan ("נדבר מחר"), or a later cancellation is NOT
an agreement.

Answer in JSON:
- agreed: true only for such an explicit agreement that wasn't cancelled later.
- date: the meeting date as YYYY-MM-DD (resolve "מחר", "יום שלישי" etc. relative to the date of the
  message that set it), "" if not agreed.
- time: HH:MM in 24-hour format, "" if not agreed.
- address: the apartment's address if it appears in the chat, else "".
- evidence: the exact chat lines (copied character for character, without the date/name prefix
  if you like) that show the time and my agreement. Empty if not agreed.
- explanation: one short sentence in Hebrew.

Conversation:
{chat}"""


class MeetingError(ValueError):
    pass


# --- chats ------------------------------------------------------------------------------

def save_chat(db, user_id, text, source="paste", title=None, post_id=None):
    """Store a chat and link it to a listing when exactly one listing shares a phone number.

    Returns {"chat_id", "post_id", "candidates": [{post_id, ...}]}.
    """
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        raise MeetingError("the chat is empty")
    phones = sorted(set(phones_in_text(f"{title or ''}\n{text}")))
    candidates = candidates_for(db, user_id, phones)
    if post_id is None and len(candidates) == 1:
        post_id = candidates[0]["post_id"]
    with db:
        cur = db.execute("INSERT INTO chats (user_id, post_id, source, title, text, phones, created_at)"
                         " VALUES (?,?,?,?,?,?,?)",
                         (user_id, post_id, source, title, text, json.dumps(phones), statuses.now_iso()))
    return {"chat_id": cur.lastrowid, "post_id": post_id, "phones": phones, "candidates": candidates}


def candidates_for(db, user_id, phones):
    """Listings whose text mentions one of these phone numbers, newest first (at most 10)."""
    if not phones:
        return []
    out = []
    rows = db.execute(f"SELECT p.post_id, p.posted_at, p.author, {EFFECTIVE_TEXT_SQL} AS text FROM posts p"
                      " WHERE " + " OR ".join(["p.text LIKE ?"] * len(phones)) + " ORDER BY p.posted_at DESC",
                      [f"%{ph[-3:]}%" for ph in phones]).fetchall()   # cheap prefilter, checked below
    for r in rows:
        if set(phones_in_text(r["text"])) & set(phones):
            out.append({"post_id": r["post_id"], "posted_at": r["posted_at"], "author": r["author"],
                        "snippet": (r["text"] or "")[:120]})
        if len(out) >= 10:
            break
    return out


def link_chat(db, user_id, chat_id, post_id):
    if not db.execute("SELECT 1 FROM posts WHERE post_id = ?", (post_id,)).fetchone():
        raise MeetingError("unknown post")
    with db:
        n = db.execute("UPDATE chats SET post_id = ? WHERE id = ? AND user_id = ?", (post_id, chat_id, user_id)).rowcount
        db.execute("UPDATE meeting_proposals SET post_id = ? WHERE chat_id = ? AND status = 'proposed'", (post_id, chat_id))
    if not n:
        raise MeetingError("unknown chat")


# --- proposal ---------------------------------------------------------------------------

def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def check_proposal(answer, chat_text, today=None):
    """The code's own checks on the AI answer. Returns (ok, reason)."""
    if not answer.get("agreed"):
        return False, answer.get("explanation") or "no agreed time in the chat"
    quotes = [q for q in answer.get("evidence") or [] if _norm(q)]
    if not quotes:
        return False, "the AI gave no quote showing the agreement"
    chat = _norm(chat_text)
    missing = [q for q in quotes if _norm(q) not in chat]
    if missing:
        return False, "a quoted line is not in the chat: " + missing[0][:80]
    try:
        when = datetime.strptime(f"{answer['date']} {answer['time']}", "%Y-%m-%d %H:%M")
    except (KeyError, ValueError):
        return False, "no exact date and time"
    if when.date() < (today or date_cls.today()) - timedelta(days=1):
        return False, "the agreed time is in the past"
    return True, None


def propose(db, cfg, user_id, chat_id, router, today=None):
    """Ask the AI for a meeting in a stored chat and store the (checked) proposal."""
    chat = db.execute("SELECT * FROM chats WHERE id = ? AND user_id = ?", (chat_id, user_id)).fetchone()
    if not chat:
        raise MeetingError("unknown chat")
    me = settings.get(db, "whatsapp_my_name")
    me_line = (f"My name in the chat is \"{me}\"." if me else
               "My own messages may appear under my WhatsApp name or as \"You\" / \"את/ה\".")
    today = today or date_cls.today()
    text = chat["text"][-MAX_CHAT_CHARS:]
    raw, _usage, _cost, label = router.json_call(
        "meeting", [{"type": "text", "text": "You read chats and extract agreed appointments. Answer in JSON."}],
        PROMPT.format(today=today.isoformat(), me=me_line, chat=text), SCHEMA)
    answer = json.loads(raw)
    ok, reason = check_proposal(answer, chat["text"], today)
    row = {"found": int(ok), "date": answer.get("date") if ok else None, "time": answer.get("time") if ok else None,
           "address": answer.get("address") or None, "evidence": json.dumps(answer.get("evidence") or [], ensure_ascii=False),
           "explanation": answer.get("explanation") if ok else reason}
    with db:
        cur = db.execute("INSERT INTO meeting_proposals (chat_id, post_id, found, date, time, address, evidence, status,"
                         " model, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (chat_id, chat["post_id"], row["found"], row["date"], row["time"], row["address"],
                          row["evidence"], "proposed" if ok else "discarded", label, statuses.now_iso()))
    return {"id": cur.lastrowid, "chat_id": chat_id, "post_id": chat["post_id"], **row,
            "evidence": json.loads(row["evidence"])}


def confirm(db, cfg, user_id, proposal_id, date, time, address=None, minutes=45, notes=None,
            create=gcal.create_event):
    """Create the calendar event for a proposal I confirmed (details may be corrected first)."""
    p = db.execute("SELECT * FROM meeting_proposals WHERE id = ?", (proposal_id,)).fetchone()
    if not p or p["status"] != "proposed":
        raise MeetingError("this proposal can't be confirmed (unknown, already confirmed or discarded)")
    if not p["post_id"]:
        raise MeetingError("link the chat to a listing first")
    start = gcal.parse_local(date, time)
    post = db.execute(f"SELECT p.post_id, p.url, p.author, {EFFECTIVE_TEXT_SQL} AS text FROM posts p"
                      " WHERE p.post_id = ?", (p["post_id"],)).fetchone()
    facts = db.execute("SELECT facts_json FROM post_analyses WHERE post_id = ? AND user_id = ? AND status = 'ok'"
                       " ORDER BY id DESC LIMIT 1", (p["post_id"], user_id)).fetchone()
    facts = json.loads(facts[0]) if facts and facts[0] else {}
    place = address or p["address"] or ", ".join(
        x for x in (facts.get("street"), facts.get("neighborhood"), facts.get("city")) if x)
    phones = phones_in_text(post["text"])
    my_notes = [r[0] for r in db.execute("SELECT body FROM post_notes WHERE post_id = ? AND user_id = ? ORDER BY id",
                                         (p["post_id"], user_id))]
    description = "\n".join(filter(None, [
        f"מפרסם/ת: {post['author']}" if post["author"] else None,
        f"טלפון: {', '.join(phones)}" if phones else None,
        f"מחיר: {facts['rent_ils']:,} ₪" if facts.get("rent_ils") else None,
        f"מודעה: {post['url']}",
        notes,
        *(f"הערה: {n}" for n in my_notes),
    ]))
    summary = "צפייה בדירה" + (f": {place}" if place else "")
    event_id, link = create(gcal.build_event(summary, start, minutes, place, description))
    with db:
        db.execute("UPDATE meeting_proposals SET status = 'confirmed', date = ?, time = ?, address = ?, event_id = ?,"
                   " event_link = ? WHERE id = ?", (date, time, place or None, event_id, link, proposal_id))
    note = f"{date} {time}" + (f", {place}" if place else "")
    statuses.set_status(db, user_id, p["post_id"], "meeting_scheduled", note, "human")
    return {"event_id": event_id, "event_link": link, "post_id": p["post_id"]}


def discard(db, proposal_id):
    with db:
        db.execute("UPDATE meeting_proposals SET status = 'discarded' WHERE id = ? AND status = 'proposed'",
                   (proposal_id,))
