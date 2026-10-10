"""Shared fixtures: a small schema-v3 database (what exists before the phone app) and a config."""
import copy
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apartment_bot import db as dbmod  # noqa: E402
import scan_facebook  # noqa: E402

NOW = datetime.now().astimezone()


def _iso(days_ago):
    return (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")


POSTS = [
    # post_id, group, author, author_id, days old, text
    ("1001", "g1", "Dana Levi", "501", 1, "דירת 3 חדרים בפלורנטין 5,000 ש\"ח 052-1234567"),
    ("1002", "g1", "Avi Cohen", "502", 2, "חדר בדירת שותפים 2,900 ש\"ח wa.me/972541112222"),
    ("1003", "g2", "Noa", None, 3, "סאבלט לחודש, בלי טלפון"),
    ("1004", "g2", "Old Post", "504", 40, "מודעה ישנה 03-5551234"),
]


def make_v3_db(path):
    """Schema as it was before the phone app (user_version 3) with a few posts and analyses."""
    db = sqlite3.connect(path)
    db.executescript(scan_facebook.SCHEMA)
    for sql in dbmod.MIGRATIONS[:3]:
        db.executescript(sql)
    db.execute("PRAGMA user_version = 3")
    db.commit()
    with db:
        for pid, group, author, author_id, days, text in POSTS:
            db.execute("INSERT INTO posts (post_id, group_id, author, author_id, posted_at, text, url, first_seen_at)"
                       " VALUES (?,?,?,?,?,?,?,?)",
                       (pid, group, author, author_id, _iso(days), text,
                        f"https://www.facebook.com/groups/{group}/posts/{pid}/", _iso(days)))
        for pid, decision, rent, city in [("1001", "PASS", 5000, "Tel Aviv"), ("1002", "NEEDS_REVIEW", 2900, "Tel Aviv"),
                                          ("1003", "REJECT", None, "Haifa")]:
            facts = {"rent_ils": rent, "city": city, "neighborhood": "Florentin" if pid == "1001" else None,
                     "listing_type": "whole_apartment", "street": None, "location_summary_en": city,
                     "location_hints": []}
            db.execute("INSERT INTO post_analyses (post_id, user_id, pipeline_version, config_hash, model, status,"
                       " final_decision, final_reason, facts_json, analyzed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (pid, "me", "p", "h", "m", "ok", decision, "reason", json.dumps(facts), _iso(0)))
        db.execute("INSERT INTO human_decisions VALUES ('1003', 'me', 'REJECT', 'too far', ?)", (_iso(1),))
    db.close()
    return path


@pytest.fixture
def v3_db(tmp_path):
    return make_v3_db(tmp_path / "apartments.db")


@pytest.fixture
def db(v3_db):
    conn, backups = dbmod.migrate(v3_db)
    assert backups
    yield conn
    conn.close()


@pytest.fixture
def cfg():
    base = yaml.safe_load((ROOT / "config.example.yaml").read_text(encoding="utf-8"))
    cfg = copy.deepcopy(base)
    cfg["users"] = cfg["users"][:1]
    cfg["users"][0]["id"] = "me"
    cfg["users"][0]["listing"]["max_post_age_days"] = 12
    cfg["facebook"]["groups"] = [{"id": "g1", "name": "Group One"}, {"id": "g2", "name": "Group Two"}]
    return cfg
