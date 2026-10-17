"""The stronger model's trip estimate for approved listings."""
import json

import pytest

from apartment_bot import commute, feed, statuses, transit


@pytest.fixture(autouse=True)
def no_real_planner(monkeypatch):
    monkeypatch.setattr(transit, "available", lambda: False)   # these tests use the model fallback


class FakeRouter:
    def __init__(self, minutes=45, scooter=False):
        self.minutes, self.scooter, self.calls = minutes, scooter, []

    def json_call(self, stage, system, content, schema, model=None):
        self.calls.append((stage, model, content))
        return json.dumps({"from_place": "פלורנטין", "minutes": self.minutes, "route": "הליכה 5 דק׳ → קו 18",
                           "uses_scooter": self.scooter, "scooter_minutes": 6, "confidence": "medium",
                           "station": "תחנת השלום", "walk_minutes": 7}), {}, 0.01, "fake"


def _user(cfg, name="שער יפו/באר יעקב", limit=60, extra=()):
    u = cfg["users"][0]
    u["commute"]["destinations"] = ([{"name": name, "max_minutes": limit, "arrive_by": "08:30", "days": "weekdays"}] if name else []) + list(extra)
    return u


def test_estimate_is_stored_and_shown_on_the_card(db, cfg):
    u, r = _user(cfg), FakeRouter(45, scooter=True)
    got = commute.check(db, cfg, u, r, "1001")[0]
    assert got["minutes"] == 45 and got["within_limit"] is True and got["scooter"] and got["scooter_minutes"] == 6
    stage, model, content = r.calls[0]
    assert (stage, model) == ("commute", "claude-sonnet-5-5") and "שער יפו/באר יעקב" in content and "Florentin" in content
    assert "08:30" in content and got["arrive_by"] == "08:30"
    c = feed.post_detail(db, cfg, "me", "1001")["commutes"][0]
    assert c["route"].startswith("הליכה") and (c["station"], c["walk_minutes"]) == ("תחנת השלום", 7)
    commute.check(db, cfg, u, r, "1001")                       # already estimated for this destination and time: no new call
    assert len(r.calls) == 1
    commute.check(db, cfg, u, r, "1001", force=True)
    assert len(r.calls) == 2
    u["commute"]["destinations"][0]["arrive_by"] = "09:00"      # a changed arrival time is a new question
    commute.check(db, cfg, u, r, "1001")
    assert len(r.calls) == 3


def test_several_destinations_each_get_an_estimate(db, cfg):
    u = _user(cfg, extra=[{"name": "הים", "max_minutes": 20, "days": "daily"}])
    r = FakeRouter(15)
    got = commute.check(db, cfg, u, r, "1001")
    assert [g["destination"] for g in got] == ["שער יפו/באר יעקב", "הים"] and got[1]["within_limit"] is True
    assert len(r.calls) == 2 and "Arrive by" not in r.calls[1][2]


def test_over_the_limit_and_unknown(db, cfg):
    u = _user(cfg, limit=60)
    assert commute.check(db, cfg, u, FakeRouter(75), "1001")[0]["within_limit"] is False
    assert commute.check(db, cfg, u, FakeRouter(0), "1002", force=True)[0]["within_limit"] is None


def test_no_destination_means_off(db, cfg):
    u = _user(cfg, name="")
    r = FakeRouter()
    assert commute.check(db, cfg, u, r, "1001") == [] and not r.calls


def test_backfill_lists_approved_without_an_estimate(db, cfg):
    u = _user(cfg)
    statuses.set_status(db, "me", "1001", "interested", None, "gpt")
    statuses.set_status(db, "me", "1002", "not_relevant", None, "human")
    assert commute.approved_without_estimate(db, "me", commute.names(u)) == ["1001"]
    commute.check(db, cfg, u, FakeRouter(), "1001")
    assert commute.approved_without_estimate(db, "me", commute.names(u)) == []


def test_old_single_destination_config_still_works(cfg):
    u = cfg["users"][0]
    u["commute"].pop("destinations", None)
    u["commute"]["destination"] = {"name": "Azrieli", "address": None}
    u["commute"]["absolute_max_minutes"] = 50
    assert commute.settings(u)["destinations"][0]["max_minutes"] == 50
