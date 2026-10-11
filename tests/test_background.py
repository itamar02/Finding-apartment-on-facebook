"""Edited posts, follow-ups, price matching, notifications and the scheduler."""
import json
from datetime import datetime, timedelta

import pytest

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
    args_seen = []
    def fake_runner(args):
        args_seen.append(args)
        calls.append(args[0])
        return 0, "done"
    sch.runner = fake_runner
    sch.tick()
    assert calls == ["scan_facebook.py", "analyze_posts.py"]
    assert args_seen[1][-2:] == ["--limit", "200"]                      # a run never pays for the whole backlog
    assert sch.state["next_run"] and not sch.state["running"] and sch.state["last_result"] == "done | done"
    sch.tick()
    assert len(calls) == 2                               # not again before the interval
    sch.run_now()
    sch.tick()
    assert len(calls) == 4


# --- emoji ---

def test_emoji_only_difference_is_not_an_edit(db):
    old = "דירת 3 חדרים בפלורנטין 5,000 ש\"ח 052-1234567"
    assert versions.record_if_changed(db, "1001", "🏠 דירת 3 חדרים ☀️ בפלורנטין 5,000 ש\"ח 📍 052-1234567", False) is None
    assert versions.record_if_changed(db, "1001", old + " גילאי 26+ 🔥", False) == "edit"


def test_duplicate_fingerprint_ignores_emoji():
    from apartment_bot.hard_rules import text_fingerprint
    assert text_fingerprint("דירה להשכרה 🌊 בתל אביב") == text_fingerprint("דירה להשכרה  בתל אביב")
    assert text_fingerprint("דירה א") != text_fingerprint("דירה ב")


# --- separate jobs and Stop ---

def _job_scheduler(db, cfg, runner):
    path = db.execute("PRAGMA database_list").fetchone()[2]
    return Scheduler(cfg, "me", path, runner=runner)


def _wait(sch, timeout=5):
    import time
    end = time.time() + timeout
    while sch.state["running"] and time.time() < end:
        time.sleep(0.02)
    assert not sch.state["running"]


def test_download_runs_no_ai_and_analyze_runs_no_scan(db, cfg):
    calls = []
    sch = _job_scheduler(db, cfg, lambda args: (calls.append(args[0]), (0, "ok"))[1])
    assert sch.start_job("download")
    _wait(sch)
    assert calls == ["scan_facebook.py"]                         # no AI call, no cost
    assert sch.start_job("analyze")
    _wait(sch)
    assert calls == ["scan_facebook.py", "analyze_posts.py"]
    assert sch.start_job("all")
    _wait(sch)
    assert calls[2:] == ["scan_facebook.py", "analyze_posts.py"]
    with pytest.raises(ValueError):
        sch.start_job("everything")


def test_stop_is_polite_the_job_finishes_by_itself(db, cfg, tmp_path, monkeypatch):
    import threading
    from apartment_bot import jobs
    monkeypatch.setattr(jobs, "STOP_FILE", tmp_path / ".stop")
    started, calls = threading.Event(), []

    class Proc:                                                   # a scan that ends when asked nicely
        pid = 1

        def poll(self):
            return 0 if self.done else None
        done = False

    proc = Proc()

    def runner(args):
        calls.append(args[0])
        sch._proc = proc
        started.set()
        import time
        while not jobs.stop_requested():                          # the script looks for the file between posts
            time.sleep(0.01)
        proc.done = True                                          # ...finishes the current post and ends
        return 0, "stopped on request"
    sch = _job_scheduler(db, cfg, runner)
    killed = []
    import apartment_bot.scheduler as scheduler_mod
    monkeypatch.setattr(scheduler_mod, "_kill_tree", lambda p: killed.append(p.pid))
    assert sch.start_job("all")
    assert not sch.start_job("download")                          # one job at a time
    assert started.wait(5)
    assert sch.stop_job(grace=5) and sch.state["stopping"]
    assert not sch.stop_job()                                     # already stopping
    _wait(sch)
    assert calls == ["scan_facebook.py"]                          # the analysis never started
    assert killed == []                                           # nothing was killed
    assert "הופסק" in sch.state["last_result"] and "מסד הנתונים תקין" in sch.state["last_result"]
    assert not jobs.stop_requested() and not sch.state["stopping"]


def test_stop_kills_only_as_a_last_resort(db, cfg, tmp_path, monkeypatch):
    import threading
    from apartment_bot import jobs
    monkeypatch.setattr(jobs, "STOP_FILE", tmp_path / ".stop")
    started, release, killed = threading.Event(), threading.Event(), []

    class Proc:
        pid = 7
        def poll(self):
            return 0 if release.is_set() else None
    import apartment_bot.scheduler as scheduler_mod
    monkeypatch.setattr(scheduler_mod, "_kill_tree", lambda p: (killed.append(p.pid), release.set()))

    def runner(args):
        sch._proc = Proc()
        started.set()
        release.wait(5)                                           # a scan that ignores the polite request
        return 1, "killed"
    sch = _job_scheduler(db, cfg, runner)
    assert sch.start_job("download") and started.wait(5)
    assert sch.stop_job(grace=0.3)
    _wait(sch)
    assert killed == [7]


def test_count_pending(db):
    # The fixture's analyses are from an older pipeline version, so 1001, 1002 and 1004 wait;
    # 1003 was rejected by me and never needs the AI.
    assert pipeline.count_pending(db, "me") == 3


def test_listings_sorted_by_an_ai_chat_are_not_sent_to_the_ai_bot(db):
    before = {p["post_id"] for p in pipeline.pending_posts(db, "me")}
    assert {"1001", "1002", "1004"} <= before and pipeline.count_pending(db, "me") == 3
    statuses.set_status(db, "me", "1001", "interested", "נראה טוב", source="gpt")      # sorted by the AI chat
    statuses.set_status(db, "me", "1002", "interested", None, source="human")           # my own choice: still analyzed
    after = {p["post_id"] for p in pipeline.pending_posts(db, "me")}
    assert after == {"1002", "1004"} and pipeline.count_pending(db, "me") == 2
    assert [p["post_id"] for p in pipeline.pending_posts(db, "me", post_ids=["1001"])] == ["1001"]   # asked for by id


def test_a_press_can_choose_how_many_posts_go_to_the_ai(db, cfg):
    seen = []
    sch = _job_scheduler(db, cfg, lambda args: (seen.append(args), (0, "ok"))[1])
    assert sch.start_job("analyze", limit=1500)
    _wait(sch)
    assert seen[-1][-2:] == ["--limit", "1500"]                       # more than the setting's 200
    assert sch.start_job("analyze", limit=0)
    _wait(sch)
    assert seen[-1][-1] == "--all"                                    # 0 = every post
    assert sch.start_job("analyze")
    _wait(sch)
    assert seen[-1][-2:] == ["--limit", "200"]                        # no choice: the setting
    with pytest.raises(ValueError):
        sch.start_job("analyze", limit=-5)


def test_a_job_started_before_a_restart_is_seen_blocks_a_second_one_and_can_be_stopped(db, cfg, tmp_path, monkeypatch):
    import os
    from apartment_bot import jobs
    monkeypatch.setattr(jobs, "STOP_FILE", tmp_path / ".stop")
    monkeypatch.setattr(jobs, "SCAN_LOCK", tmp_path / ".scan.lock")
    monkeypatch.setattr(jobs, "ANALYZE_LOCK", tmp_path / ".analyze.lock")
    sch = _job_scheduler(db, cfg, lambda args: (0, "ok"))
    assert jobs.external_job() is None and sch.view()["running"] is False
    (tmp_path / ".analyze.lock").write_text(str(os.getpid()))          # an analysis of an earlier server is alive
    assert jobs.external_job() == "analyze"
    v = sch.view()
    assert v["running"] and v["job"] == "analyze" and not v["stopping"]
    assert not sch.start_job("analyze")                                 # a second one would spend the money twice
    assert sch.stop_job() and jobs.stop_requested() and sch.view()["stopping"]
    (tmp_path / ".analyze.lock").write_text("99999999")                 # a dead process's lock is ignored
    assert jobs.external_job() is None


def test_two_analyses_cannot_run_at_once(tmp_path, monkeypatch):
    from apartment_bot import jobs
    monkeypatch.setattr(jobs, "ANALYZE_LOCK", tmp_path / ".analyze.lock")
    assert jobs.acquire_analyze_lock()
    (tmp_path / ".analyze.lock").write_text("1")                        # another live process (pid 1 may be absent)
    monkeypatch.setattr(jobs, "_pid_alive", lambda pid: True)
    assert not jobs.acquire_analyze_lock()
    monkeypatch.setattr(jobs, "_pid_alive", lambda pid: False)          # it died: its lock is replaced
    assert jobs.acquire_analyze_lock()
    jobs.release_analyze_lock()
    assert not (tmp_path / ".analyze.lock").exists()
