"""Edited posts, follow-ups, price matching, notifications and the scheduler."""
import json
from datetime import datetime, timedelta

from apartment_bot import followups, notify, pipeline, prices, settings, statuses, versions
from apartment_bot.scheduler import Scheduler

NOW = datetime.now().astimezone()


# --- edited posts ---

def test_edit_creates_version_and_reanalysis(db):
    assert versions.record_if_changed(db, "1001", "דירת 3 חדרים בפלורנטין 5,000 ש\"ח 052-1234567", False) is None
    kind = versions.record_if_changed(db, "1001", "דירת 3 חדרים בפלורנטין 4,500 ש\"ח 052-1234567 גילאי 26+", False)
    assert kind == "edit"
    assert versions.current_text(db, "1001")[0].endswith("26+")
    assert db.execute("SELECT text FROM posts WHERE post_id = '1001'").fetchone()[0].endswith("052-1234567")


def test_edited_post_is_pending_again(db, monkeypatch):
    monkeypatch.setattr(pipeline.ai_filter, "PIPELINE_VERSION", "p")
    assert pipeline.pending_posts(db, "me", post_ids=["1001"]) == []
    versions.record_if_changed(db, "1001", "טקסט חדש לגמרי", False, now=NOW + timedelta(minutes=5))
    pending = pipeline.pending_posts(db, "me", post_ids=["1001"])
    assert [p["post_id"] for p in pending] == ["1001"] and pending[0]["text"] == "טקסט חדש לגמרי"


def test_truncated_reads_and_expansions(db):
    assert versions.record_if_changed(db, "1002", "חדר", True) is None          # read less: no change
    db.execute("UPDATE posts SET text_truncated = 1, text = 'חדר בדירת שותפים 2,900 ש\"ח בקומה שלישית עם' "
               "WHERE post_id = '1002'")
    full = "חדר בדירת שותפים 2,900 ש\"ח בקומה שלישית עם מרפסת גדולה ומזגן בכל חדר"
    assert versions.record_if_changed(db, "1002", full, False) == "expanded"


def test_change_summary():
    s = versions.change_summary("מחפשים שותף להצטרף", "מחפשים שותף גילאי 26+ להצטרף")
    assert "נוסף: גילאי 26+" in s


# --- follow-ups ---

def test_followups_due_snooze_dismiss(db):
    statuses.set_status(db, "me", "1001", "contacted")
    statuses.set_status(db, "me", "1002", "interested")
    later = NOW + timedelta(hours=49)
    assert followups.due(db, "me", NOW) == []
    due = followups.due(db, "me", later)
    assert [d["post_id"] for d in due] == ["1001"] and due[0]["overdue_hours"] >= 1
    followups.snooze(db, "me", "1001", 24, now=later)
    assert followups.due(db, "me", later + timedelta(hours=1)) == []
    assert len(followups.due(db, "me", later + timedelta(hours=25))) == 1
    followups.dismiss(db, "me", "1001")
    assert followups.due(db, "me", later + timedelta(hours=100)) == []
    statuses.set_status(db, "me", "1001", "awaiting_reply")        # new status period: new clock
    assert len(followups.due(db, "me", NOW + timedelta(hours=80))) == 1


# --- price tracking ---

def _post(db, pid, author_id, text, rent, days_ago):
    when = (NOW - timedelta(days=days_ago)).isoformat(timespec="seconds")
    db.execute("INSERT INTO posts (post_id, group_id, author, author_id, posted_at, text, url, first_seen_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (pid, "g1", "x", author_id, when, text, "u", when))
    db.execute("INSERT INTO post_analyses (post_id, user_id, pipeline_version, config_hash, model, status,"
               " final_decision, facts_json, analyzed_at) VALUES (?,?,?,?,?,?,?,?,?)",
               (pid, "me", "p", "h", "m", "ok", "PASS", json.dumps({"rent_ils": rent}), when))
    db.commit()


BASE = "דירת 3 חדרים ברחוב הרצל 10 תל אביב, משופצת, מרפסת שמש, קומה 2 עם מעלית, כניסה מיידית. 054-7778888"


def test_same_phone_and_similar_text_is_one_listing(db):
    _post(db, "2001", "a1", BASE + " מחיר 6,000", 6000, 5)
    _post(db, "2002", "a2", BASE + " מחיר 5,500 בלבד!", 5500, 1)                      # repost, new price
    _post(db, "2003", "a3", "דירה אחרת לגמרי ברמת גן 054-7778888 4 חדרים גדולה", 7000, 1)  # same phone, other flat
    drops = prices.update(db, "me", min_drop=200)
    assert drops == [{"listing_id": "2001", "post_id": "2002", "old_rent": 6000, "new_rent": 5500}]
    assert [h["rent_ils"] for h in prices.history(db, "2002")] == [6000, 5500]
    assert prices.history(db, "2003") == []
    assert prices.update(db, "me") == []                          # idempotent


def test_same_author_similar_text(db):
    _post(db, "3001", "auth", BASE.replace("054-7778888", ""), 6000, 3)
    _post(db, "3002", "auth", BASE.replace("054-7778888", "") + " עדיין רלוונטי", 6000, 1)
    prices.update(db, "me")
    assert len(prices.history(db, "3002")) == 2


# --- notifications ---

def test_quiet_hours():
    q = {"start": "23:00", "end": "07:00"}
    at = lambda h, m=0: NOW.replace(hour=h, minute=m)
    assert notify.in_quiet_hours(q, at(23, 30)) and notify.in_quiet_hours(q, at(3))
    assert not notify.in_quiet_hours(q, at(7)) and not notify.in_quiet_hours(q, at(12))
    assert notify.in_quiet_hours({"start": "13:00", "end": "14:00"}, at(13, 30))
    assert not notify.in_quiet_hours(None, at(3))


def test_new_listing_events_respect_settings(db, cfg):
    settings.put(db, {"notifications": {"price_ceiling": 4000}})
    since = (NOW - timedelta(days=1)).isoformat()
    assert notify.collect_events(db, cfg, "me", since) == 1          # only 1002 (2,900); 1001 is 5,000
    n = db.execute("SELECT kind, post_id, title FROM notifications").fetchone()
    assert n["kind"] == "new_listing" and n["post_id"] == "1002" and "2,900" in n["title"]
    assert notify.collect_events(db, cfg, "me", since) == 0          # once only


def test_edit_event_only_for_seen_posts(db, cfg):
    since = (NOW - timedelta(minutes=1)).isoformat()
    settings.put(db, {"notifications": {"decisions": []}})
    versions.record_if_changed(db, "1001", "נוסח חדש לגמרי", False, now=NOW)
    assert notify.collect_events(db, cfg, "me", since) == 0           # never seen
    db.execute("INSERT INTO post_seen VALUES ('1002', 'me', ?, ?)", ((NOW - timedelta(days=1)).isoformat(),) * 2)
    versions.record_if_changed(db, "1002", "חדר בדירת שותפים גילאי 26+", False, now=NOW)
    db.commit()
    assert notify.collect_events(db, cfg, "me", since) == 1
    assert "26+" in db.execute("SELECT body FROM notifications WHERE kind = 'post_edited'").fetchone()[0]


def test_delivery_push_quiet_hours_and_errors(db):
    notify.queue(db, "me", "test", "t1", "כותרת", "גוף", "1001")
    db.execute("INSERT INTO push_subscriptions VALUES ('https://push/1', 'me', '{}', 'x')")
    db.commit()
    sent = []
    sender = lambda sub, payload: sent.append(json.loads(payload))
    settings.put(db, {"notifications": {"quiet_hours": {"start": "00:00", "end": "23:59"}}})
    assert notify.deliver_pending(db, "me", NOW.replace(hour=12), push_sender=sender) == 0
    settings.put(db, {"notifications": {"quiet_hours": None}})
    assert notify.deliver_pending(db, "me", push_sender=sender) == 1
    assert sent[0]["url"] == "/#post=1001"
    assert notify.deliver_pending(db, "me", push_sender=sender) == 0           # already sent

    def broken(sub, payload):
        raise RuntimeError("down")
    notify.queue(db, "me", "test", "t2", "a", "b")
    assert notify.deliver_pending(db, "me", push_sender=broken) == 0
    assert "down" in db.execute("SELECT error FROM notifications WHERE dedupe_key = 't2'").fetchone()[0]


# --- scheduler ---

def test_scheduler_runs_scan_then_analysis(db, cfg):
    path = db.execute("PRAGMA database_list").fetchone()[2]
    calls = []
    sch = Scheduler(cfg, "me", path, runner=lambda args: (calls.append(args[0]), (0, "done\n"))[1])
    sch.tick()
    assert calls == []                                   # off by default
    settings.put(db, {"scheduler": {"enabled": True, "quiet_hours": None}})
    sch.tick()
    assert calls == ["scan_facebook.py", "analyze_posts.py"]
    assert sch.state["next_run"] and not sch.state["running"] and sch.state["last_result"] == "done | done"
    sch.tick()
    assert len(calls) == 2                               # not again before the interval
    sch.run_now()
    sch.tick()
    assert len(calls) == 4
