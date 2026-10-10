"""Visit checklist and the chat -> proposal -> confirmed calendar event flow."""
import json
from datetime import date

import pytest

from apartment_bot import gcal, meetings, statuses, visits

CHAT = """[09/10/2026, 18:01:02] Dana: היי, הדירה עדיין פנויה. אפשר לבוא לראות מחר ב-18:30
[09/10/2026, 18:05:11] Me: מעולה, סגור. נתראה מחר ב-18:30
[09/10/2026, 18:06:00] Dana: הכתובת: פלורנטין 12, תל אביב. הטלפון שלי 052-1234567"""


class FakeRouter:
    def __init__(self, answer):
        self.answer = answer
        self.stages = []

    def json_call(self, stage, system, content, schema):
        self.stages.append(stage)
        return json.dumps(self.answer, ensure_ascii=False), {}, 0.0, "fake"


AGREED = {"agreed": True, "date": "2026-10-10", "time": "18:30", "address": "פלורנטין 12, תל אביב",
          "evidence": ["מעולה, סגור. נתראה מחר ב-18:30"], "explanation": "סוכם"}


def test_visit_checklist_rating_and_comparison(db):
    statuses.set_status(db, "me", "1001", "visited")
    items = visits.items(db)
    assert len(items) == 7
    v = visits.save(db, "me", "1001", rating=4, notes="שקט", checks=[
        {"item_id": items[0]["id"], "state": "ok"}, {"item_id": items[1]["id"], "state": "issue", "note": "בחדר שינה"}])
    assert v["rating"] == 4 and v["checklist"][1]["state"] == "issue"
    with pytest.raises(ValueError):
        visits.save(db, "me", "1001", rating=7)
    comp = visits.comparison(db, "me")
    assert comp[0]["post_id"] == "1001" and comp[0]["issues"] == [{"label": items[1]["label"], "note": "בחדר שינה"}]


def test_checklist_items_editable_and_old_answers_kept(db):
    first = visits.items(db)[0]
    visits.save(db, "me", "1001", checks=[{"item_id": first["id"], "state": "ok"}])
    new = visits.save_items(db, ["מרפסת", "רעש"])
    assert [i["label"] for i in new] == ["מרפסת", "רעש"]
    labels = [c["label"] for c in visits.get(db, "me", "1001")["checklist"]]
    assert first["label"] in labels                    # answered before: still shown for this visit


def test_visit_photos(db, tmp_path, monkeypatch):
    monkeypatch.setattr(visits, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(visits, "UPLOADS", tmp_path / "uploads" / "visits")
    pid = visits.add_photo(db, "me", "1001", b"\xff\xd8 jpeg bytes", "image/jpeg")
    assert visits.photo_path(db, "me", pid).read_bytes().startswith(b"\xff\xd8")
    with pytest.raises(ValueError):
        visits.add_photo(db, "me", "1001", b"<html>", "text/html")
    assert visits.delete_photo(db, "me", pid) and visits.photo_path(db, "me", pid) is None


def test_chat_links_by_phone(db):
    saved = meetings.save_chat(db, "me", CHAT)
    assert saved["phones"] == ["052-1234567"] and saved["post_id"] == "1001"
    other = meetings.save_chat(db, "me", "שלום, בלי טלפון")
    assert other["post_id"] is None and other["candidates"] == []
    meetings.link_chat(db, "me", other["chat_id"], "1002")
    assert db.execute("SELECT post_id FROM chats WHERE id = ?", (other["chat_id"],)).fetchone()[0] == "1002"


def test_check_proposal():
    today = date(2026, 10, 9)
    assert meetings.check_proposal(AGREED, CHAT, today) == (True, None)
    assert not meetings.check_proposal({**AGREED, "agreed": False, "explanation": "לא סוכם"}, CHAT, today)[0]
    bad_quote = {**AGREED, "evidence": ["נתראה ב-20:00"]}
    assert "not in the chat" in meetings.check_proposal(bad_quote, CHAT, today)[1]
    assert not meetings.check_proposal({**AGREED, "evidence": []}, CHAT, today)[0]
    assert not meetings.check_proposal({**AGREED, "time": "evening"}, CHAT, today)[0]
    assert "past" in meetings.check_proposal({**AGREED, "date": "2026-09-01"}, CHAT, today)[1]


def test_propose_never_creates_an_event_and_confirm_does(db, cfg):
    chat = meetings.save_chat(db, "me", CHAT)
    router = FakeRouter(AGREED)
    p = meetings.propose(db, cfg, "me", chat["chat_id"], router, today=date(2026, 10, 9))
    assert router.stages == ["meeting"] and p["found"] == 1 and p["time"] == "18:30"
    assert statuses.current(db, "me", "1001") is None                 # nothing happens before Confirm
    created = []

    def fake_create(event):
        created.append(event)
        return "evt1", "https://calendar/evt1"
    statuses.set_status(db, "me", "1001", "contacted")
    r = meetings.confirm(db, cfg, "me", p["id"], "2026-10-10", "18:45", None, 30, "להביא תעודה", create=fake_create)
    assert r == {"event_id": "evt1", "event_link": "https://calendar/evt1", "post_id": "1001"}
    ev = created[0]
    assert ev["start"]["dateTime"] == "2026-10-10T18:45:00" and ev["end"]["dateTime"] == "2026-10-10T19:15:00"
    assert ev["location"] == "פלורנטין 12, תל אביב" and "052-1234567" in ev["description"]
    assert "facebook.com/groups/g1/posts/1001" in ev["description"] and "להביא תעודה" in ev["description"]
    assert statuses.current(db, "me", "1001")["status"] == "meeting_scheduled"
    with pytest.raises(meetings.MeetingError):
        meetings.confirm(db, cfg, "me", p["id"], "2026-10-10", "18:45", create=fake_create)   # only once
    assert len(created) == 1


def test_no_agreement_no_proposal(db, cfg):
    chat = meetings.save_chat(db, "me", "Dana: אפשר מחר?\nMe: אבדוק ואחזור אלייך")
    p = meetings.propose(db, cfg, "me", chat["chat_id"],
                         FakeRouter({"agreed": False, "date": "", "time": "", "address": "", "evidence": [],
                                     "explanation": "לא נקבע מועד"}))
    assert p["found"] == 0
    with pytest.raises(meetings.MeetingError):
        meetings.confirm(db, cfg, "me", p["id"], "2026-10-10", "18:00", create=lambda e: ("x", "y"))


def test_build_event_and_parse():
    with pytest.raises(gcal.CalendarError):
        gcal.parse_local("10/10/2026", "6pm")
    ev = gcal.build_event("צפייה", gcal.parse_local("2026-10-10", "23:30"), 60)
    assert ev["end"]["dateTime"] == "2026-10-11T00:30:00" and ev["start"]["timeZone"] == "Asia/Jerusalem"
