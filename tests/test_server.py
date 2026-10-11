"""The app API: login, feed filters, statuses, notes, settings."""
import pytest
from fastapi.testclient import TestClient

from apartment_bot import auth, server

W = {"X-ApartmentBot": "1"}


@pytest.fixture
def client(db, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SECRETS_DIR", tmp_path / "secrets")
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "secrets" / "app_token.txt")
    monkeypatch.setattr(auth, "PAIRING_FILE", tmp_path / "secrets" / "pairing.json")
    db_path = db.execute("PRAGMA database_list").fetchone()[2]
    app = server.create_app(cfg, "me", db_path=db_path, web_dist=tmp_path / "dist", secure_cookie=False,
                            run_scheduler=False)
    c = TestClient(app)
    code = auth.new_pairing_code()
    assert c.post("/api/pair", json={"code": code}).status_code == 200
    return c


def test_needs_pairing(db, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SECRETS_DIR", tmp_path / "s")
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "s" / "t.txt")
    monkeypatch.setattr(auth, "PAIRING_FILE", tmp_path / "s" / "p.json")
    db_path = db.execute("PRAGMA database_list").fetchone()[2]
    c = TestClient(server.create_app(cfg, "me", db_path=db_path, web_dist=tmp_path, secure_cookie=False,
                                     run_scheduler=False))
    assert c.get("/api/meta").status_code == 401
    code = auth.new_pairing_code()
    assert c.post("/api/pair", json={"code": "00000000" if code != "00000000" else "1"}).status_code == 403
    assert c.post("/api/pair", json={"code": code}).status_code == 200
    assert c.get("/api/meta").status_code == 200
    assert c.post("/api/pair", json={"code": code}).status_code == 403          # one use only
    fresh = TestClient(c.app)
    assert fresh.get("/api/meta").status_code == 401
    assert fresh.get("/api/meta", headers={"Authorization": f"Bearer {auth.get_token()}"}).status_code == 200


def test_pairing_code_locks_after_wrong_tries(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SECRETS_DIR", tmp_path)
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "t.txt")
    monkeypatch.setattr(auth, "PAIRING_FILE", tmp_path / "p.json")
    code = auth.new_pairing_code()
    wrong = f"{(int(code) + 1) % 10**8:08d}"
    for _ in range(auth.PAIRING_TRIES):
        assert auth.redeem_pairing_code(wrong) is None
    assert auth.redeem_pairing_code(code) is None


def test_writes_need_header(client):
    assert client.put("/api/posts/1001/status", json={"status": "interested"}).status_code == 403
    assert client.put("/api/posts/1001/status", json={"status": "interested"}, headers=W).status_code == 200


def test_feed_defaults_hide_old_posts(client):
    ids = [p["post_id"] for p in client.get("/api/posts").json()["items"]]
    assert ids == ["1001", "1002", "1003"]
    ids = [p["post_id"] for p in client.get("/api/posts?include_old=true").json()["items"]]
    assert "1004" in ids


def test_feed_filters(client):
    get = lambda q: [p["post_id"] for p in client.get("/api/posts?" + q).json()["items"]]
    assert get("decision=PASS") == ["1001"]
    assert get("decision=PENDING&include_old=true") == ["1004"]
    assert get("status=not_relevant") == ["1003"]
    assert get("status=none") == ["1001", "1002"]
    assert get("group=g2") == ["1003"]
    assert get("price_min=3000") == ["1001"]
    assert get("price_max=3000") == ["1002"]
    assert get("area=florentin") == ["1001"]
    assert get("has_phone=true") == ["1001", "1002"]
    assert get("sort=price_asc&decision=PASS&decision=NEEDS_REVIEW") == ["1002", "1001"]
    assert get("source=human") == ["1003"]
    assert client.get("/api/posts?sort=bad").status_code == 400


def test_card_contents(client):
    card = client.get("/api/posts?decision=PASS").json()["items"][0]
    assert card["group_name"] == "Group One"
    assert card["ai"]["decision"] == "PASS"
    assert card["action"]["kind"] == "whatsapp" and "972521234567" in card["action"]["url"]
    assert card["facts"]["rent_ils"] == 5000


def test_contacted_and_undo(client):
    r = client.post("/api/posts/1001/contacted", headers=W).json()
    assert r["previous"] is None and r["status"]["status"] == "contacted"
    r = client.post("/api/posts/1001/status/undo", headers=W).json()
    assert r["status"] is None


def test_detail_marks_seen_and_notes(client, db):
    assert client.post("/api/posts/1001/notes", json={"body": "  נראה טוב "}, headers=W).status_code == 200
    d = client.get("/api/posts/1001").json()
    assert d["notes"][0]["body"] == "נראה טוב"
    assert db.execute("SELECT 1 FROM post_seen WHERE post_id = '1001'").fetchone()
    nid = d["notes"][0]["id"]
    assert client.put(f"/api/notes/{nid}", json={"body": "x"}, headers=W).status_code == 200
    assert client.delete(f"/api/notes/{nid}", headers=W).status_code == 200
    assert client.get("/api/posts/9999").status_code == 404


def test_settings_and_status_list(client):
    s = client.put("/api/settings", json={"whatsapp_template": "שלום {name}"}, headers=W).json()
    assert s["whatsapp_template"] == "שלום {name}"
    assert client.put("/api/settings", json={"nope": 1}, headers=W).status_code == 400
    r = client.post("/api/statuses", json={"label": "ביקור שני", "key": "second_visit"}, headers=W).json()
    assert r["key"] == "second_visit"
    assert client.delete("/api/statuses/new", headers=W).status_code == 400
    meta = client.get("/api/meta").json()
    assert meta["recent_days"] == 12 and len(meta["statuses"]) == 12


def test_web_app_not_built(client):
    assert client.get("/").status_code == 503


def test_server_starts_with_scheduler(db, cfg, tmp_path):
    db_path = db.execute("PRAGMA database_list").fetchone()[2]
    app = server.create_app(cfg, "me", db_path=db_path, web_dist=tmp_path, secure_cookie=False)
    with TestClient(app) as c:                     # runs the startup / shutdown hooks
        assert c.get("/").status_code == 503
        assert app.state.scheduler._thread.is_alive()


def test_feed_can_hide_closed_statuses(client):
    ids = lambda q: [p["post_id"] for p in client.get("/api/posts?" + q).json()["items"]]
    assert ids("") == ["1001", "1002", "1003"]
    assert ids("exclude_status=not_relevant") == ["1001", "1002"]          # 1003 was marked not relevant
    assert ids("exclude_status=not_relevant&status=none") == ["1001", "1002"]


def test_listings_with_my_status_stay_whatever_the_ai_said(client):
    ids = lambda q: [p["post_id"] for p in client.get("/api/posts?" + q).json()["items"]]
    assert ids("decision=PASS") == ["1001"]
    client.put("/api/posts/1003/status", json={"status": "interested"}, headers=W)       # 1003 was rejected by the AI
    assert ids("decision=PASS") == ["1001"]                                              # not without the option
    assert ids("decision=PASS&with_status=true") == ["1001", "1003"]                     # an open status keeps it
    client.put("/api/posts/1003/status", json={"status": "not_relevant"}, headers=W)
    assert ids("decision=PASS&with_status=true") == ["1001"]                             # a closed one doesn't


def test_my_location_replaces_the_ai_reading_and_can_be_cleared(client):
    card = lambda: client.get("/api/posts?decision=PASS").json()["items"][0]
    assert card()["location"]["text"] == "Florentin, Tel Aviv" and not card()["location"]["mine"]
    r = client.put("/api/posts/1001/location", json={"location": "הרצל 10, פלורנטין"}, headers=W).json()
    assert r["location"] == {**r["location"], "text": "הרצל 10, פלורנטין", "mine": True, "ai_text": "Florentin, Tel Aviv"}
    assert client.get("/api/posts?area=%D7%94%D7%A8%D7%A6%D7%9C").json()["total"] == 1       # the area filter sees it
    r = client.put("/api/posts/1001/location", json={"location": "  "}, headers=W).json()
    assert r["location"]["text"] == "Florentin, Tel Aviv" and not r["location"]["mine"]
    assert client.put("/api/posts/9999/location", json={"location": "x"}, headers=W).status_code == 404


def test_add_to_calendar_creates_one_event_and_sets_the_status(client, monkeypatch):
    from apartment_bot import gcal
    made = []
    monkeypatch.setattr(gcal, "create_event", lambda ev, session=None: (made.append(ev), ("e1", "https://cal/e1"))[1])
    client.put("/api/posts/1001/location", json={"location": "הרצל 10"}, headers=W)
    r = client.post("/api/posts/1001/calendar", json={"date": "2026-10-20", "time": "18:30", "minutes": 30}, headers=W)
    assert r.status_code == 200 and r.json()["event_link"] == "https://cal/e1"
    assert len(made) == 1 and made[0]["location"] == "הרצל 10"                  # my corrected location is the address
    assert made[0]["start"]["dateTime"] == "2026-10-20T18:30:00" and made[0]["end"]["dateTime"] == "2026-10-20T19:00:00"
    assert client.get("/api/posts?status=meeting_scheduled").json()["total"] == 1
    assert client.post("/api/posts/1001/calendar", json={"date": "tomorrow", "time": "18:30"}, headers=W).status_code == 400
    assert len(made) == 1                                                       # a bad date creates nothing


def test_my_price_replaces_the_ai_reading_in_the_card_filters_and_sorting(client):
    get = lambda q: client.get("/api/posts?" + q).json()["items"]
    card = [c for c in get("decision=PASS") if c["post_id"] == "1001"][0]
    assert card["price"] == {"rent": 5000, "total": None, "mine": False, "ai_rent": 5000}
    r = client.put("/api/posts/1001/price", json={"rent": 4500}, headers=W).json()
    assert r["price"] == {"rent": 4500, "total": None, "mine": True, "ai_rent": 5000}
    assert [c["post_id"] for c in get("price_min=3000&price_max=4600")] == ["1001"]       # filters use my price (4,500)
    assert get("price_min=4800") == []                                                     # not the AI's 5,000
    assert [c["post_id"] for c in get("sort=price_asc&decision=PASS&decision=NEEDS_REVIEW")] == ["1002", "1001"]
    r = client.put("/api/posts/1001/price", json={"rent": None}, headers=W).json()
    assert r["price"]["rent"] == 5000 and not r["price"]["mine"]
    assert client.put("/api/posts/1001/price", json={"rent": 99999999}, headers=W).status_code == 400
    assert client.put("/api/posts/9999/price", json={"rent": 3000}, headers=W).status_code == 404


def test_bills_included_or_not_from_the_post_and_by_hand(client):
    from apartment_bot.feed import bills_info
    inc = {"additional_costs": [{"kind": "utilities", "included_in_rent": "yes", "amount_ils": None},
                                {"kind": "cleaning", "included_in_rent": "no", "amount_ils": 100}]}
    exc = {"additional_costs": [{"kind": "arnona", "included_in_rent": "no", "amount_ils": 400},
                                {"kind": "internet", "included_in_rent": "yes", "amount_ils": None}]}
    assert bills_info(inc)["state"] == "included"                        # cleaning isn't a bill
    assert bills_info(exc)["state"] == "excluded" and bills_info(exc)["extras"] == [{"kind": "arnona", "amount": 400}]
    assert bills_info({})["state"] == "unknown" and bills_info(None)["state"] == "unknown"
    assert bills_info(exc, "included") == {**bills_info(exc, "included"), "state": "included", "mine": True,
                                           "ai_state": "excluded"}
    c = lambda: [x for x in client.get("/api/posts?decision=PASS").json()["items"] if x["post_id"] == "1001"][0]
    assert c()["bills"]["state"] == "unknown"
    r = client.put("/api/posts/1001/price", json={"rent": None, "bills": "excluded"}, headers=W).json()
    assert r["bills"]["state"] == "excluded" and r["bills"]["mine"] and r["price"]["rent"] == 5000
    assert client.put("/api/posts/1001/price", json={"rent": 4800, "bills": "maybe"}, headers=W).status_code == 400
    r = client.put("/api/posts/1001/price", json={"rent": None, "bills": None}, headers=W).json()
    assert r["bills"]["state"] == "unknown" and not r["bills"]["mine"]
