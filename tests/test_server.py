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
    app = server.create_app(cfg, "me", db_path=db_path, web_dist=tmp_path / "dist", secure_cookie=False)
    c = TestClient(app)
    code = auth.new_pairing_code()
    assert c.post("/api/pair", json={"code": code}).status_code == 200
    return c


def test_needs_pairing(db, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "SECRETS_DIR", tmp_path / "s")
    monkeypatch.setattr(auth, "TOKEN_FILE", tmp_path / "s" / "t.txt")
    monkeypatch.setattr(auth, "PAIRING_FILE", tmp_path / "s" / "p.json")
    db_path = db.execute("PRAGMA database_list").fetchone()[2]
    c = TestClient(server.create_app(cfg, "me", db_path=db_path, web_dist=tmp_path, secure_cookie=False))
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
    assert meta["recent_days"] == 12 and len(meta["statuses"]) == 11


def test_web_app_not_built(client):
    assert client.get("/").status_code == 503
