"""The stronger model's trip estimate for approved listings."""
import json

from apartment_bot import commute, feed, statuses


class FakeRouter:
    def __init__(self, minutes=45, scooter=False):
        self.minutes, self.scooter, self.calls = minutes, scooter, []

    def json_call(self, stage, system, content, schema, model=None):
        self.calls.append((stage, model, content))
        return json.dumps({"from_place": "פלורנטין", "minutes": self.minutes, "route": "הליכה 5 דק׳ → קו 18",
                           "uses_scooter": self.scooter, "scooter_minutes": 6, "confidence": "medium"}), {}, 0.01, "fake"


def _user(cfg, name="שער יפו/באר יעקב", limit=60):
    u = cfg["users"][0]
    u["commute"]["destination"] = {"name": name, "address": None}
    u["commute"]["absolute_max_minutes"] = limit
    return u


def test_estimate_is_stored_and_shown_on_the_card(db, cfg):
    u, r = _user(cfg), FakeRouter(45, scooter=True)
    got = commute.check(db, cfg, u, r, "1001")
    assert got["minutes"] == 45 and got["within_limit"] is True and got["scooter"] and got["scooter_minutes"] == 6
    stage, model, content = r.calls[0]
    assert (stage, model) == ("commute", "claude-sonnet-5-5") and "שער יפו/באר יעקב" in content and "Florentin" in content
    assert feed.post_detail(db, cfg, "me", "1001")["commute"]["route"].startswith("הליכה")
    commute.check(db, cfg, u, r, "1001")                       # already estimated for this destination: no new call
    assert len(r.calls) == 1
    commute.check(db, cfg, u, r, "1001", force=True)
    assert len(r.calls) == 2


def test_over_the_limit_and_unknown(db, cfg):
    u = _user(cfg, limit=60)
    assert commute.check(db, cfg, u, FakeRouter(75), "1001")["within_limit"] is False
    assert commute.check(db, cfg, u, FakeRouter(0), "1002", force=True)["within_limit"] is None


def test_no_destination_means_off(db, cfg):
    u = _user(cfg, name="")
    r = FakeRouter()
    assert commute.check(db, cfg, u, r, "1001") is None and not r.calls


def test_backfill_lists_approved_without_an_estimate(db, cfg):
    u = _user(cfg)
    statuses.set_status(db, "me", "1001", "interested", None, "gpt")
    statuses.set_status(db, "me", "1002", "not_relevant", None, "human")
    assert commute.approved_without_estimate(db, "me", "שער יפו/באר יעקב") == ["1001"]
    commute.check(db, cfg, u, FakeRouter(), "1001")
    assert commute.approved_without_estimate(db, "me", "שער יפו/באר יעקב") == []
