"""Migration 4 and the status model, including the sync with reject.py's human_decisions."""
import sqlite3

import pytest

from apartment_bot import db as dbmod, human, statuses


def test_migration_moves_hand_rejections_to_not_relevant(v3_db):
    conn, backups = dbmod.migrate(v3_db)
    assert len(backups) == 1 and "backup-v3" in backups[0]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(dbmod.MIGRATIONS)
    row = statuses.current(conn, "me", "1003")
    assert row["status"] == "not_relevant" and row["source"] == "human" and row["note"] == "too far"
    assert len(statuses.history(conn, "me", "1003")) == 1
    assert conn.execute("SELECT count(*) FROM human_decisions").fetchone()[0] == 1   # kept as is
    assert len(statuses.list_statuses(conn)) == 10


def test_migration_is_idempotent(v3_db):
    dbmod.migrate(v3_db)[0].close()
    conn, backups = dbmod.migrate(v3_db)
    assert backups == []


def test_posts_and_analyses_untouched(v3_db):
    before = sqlite3.connect(v3_db)
    snap = (before.execute("SELECT * FROM posts ORDER BY post_id").fetchall(),
            before.execute("SELECT * FROM post_analyses ORDER BY id").fetchall())
    before.close()
    conn, _ = dbmod.migrate(v3_db)
    assert [tuple(r) for r in conn.execute("SELECT * FROM posts ORDER BY post_id")] == [tuple(r) for r in snap[0]]
    assert [tuple(r) for r in conn.execute("SELECT * FROM post_analyses ORDER BY id")] == [tuple(r) for r in snap[1]]


def test_set_status_writes_history_and_undo(db):
    statuses.set_status(db, "me", "1001", "interested")
    statuses.set_status(db, "me", "1001", "needs_details", "ask about parking")
    cur = statuses.current(db, "me", "1001")
    assert cur["status"] == "needs_details" and cur["note"] == "ask about parking"
    hist = statuses.history(db, "me", "1001")
    assert [h["status"] for h in hist] == ["interested", "needs_details"]
    assert hist[1]["previous_status"] == "interested"
    statuses.undo(db, "me", "1001")
    assert statuses.current(db, "me", "1001")["status"] == "interested"
    assert len(statuses.history(db, "me", "1001")) == 3   # undo is a change too


def test_clear_status(db):
    statuses.set_status(db, "me", "1001", "interested")
    statuses.set_status(db, "me", "1001", None)
    assert statuses.current(db, "me", "1001") is None
    statuses.undo(db, "me", "1001")
    assert statuses.current(db, "me", "1001")["status"] == "interested"


def test_unknown_status_and_post_rejected(db):
    with pytest.raises(statuses.StatusError):
        statuses.set_status(db, "me", "1001", "nope")
    with pytest.raises(statuses.StatusError):
        statuses.set_status(db, "me", "9999", "interested")
    with pytest.raises(statuses.StatusError):
        statuses.record(db, "me", "1001", "interested", source="robot")


def test_not_relevant_syncs_human_decisions(db):
    statuses.set_status(db, "me", "1001", "not_relevant", "ground floor")
    assert human.rejected(db, "me")["1001"][0] == "ground floor"
    statuses.set_status(db, "me", "1001", "interested")
    assert "1001" not in human.rejected(db, "me")


def test_reject_py_path_sets_status(db):
    done, unknown = human.reject(db, "me", ["1002", "4242"], note="taken")
    assert done == ["1002"] and unknown == ["4242"]
    assert statuses.current(db, "me", "1002")["status"] == "not_relevant"
    assert human.undo(db, "me", ["1002"]) == 1
    assert statuses.current(db, "me", "1002") is None


def test_reject_undo_keeps_other_status(db):
    human.reject(db, "me", ["1002"])
    statuses.record(db, "me", "1002", "taken")   # status moved on without touching human_decisions
    human.undo(db, "me", ["1002"])
    assert statuses.current(db, "me", "1002")["status"] == "taken"


def test_edit_status_list(db):
    key = statuses.save_status(db, "Second visit", "ביקור שני", "#ff0000")
    assert key == "second_visit"
    statuses.save_status(db, "new", "חדש לגמרי", "#000000")
    assert {s["key"]: s["label"] for s in statuses.list_statuses(db)}["new"] == "חדש לגמרי"
    keys = [s["key"] for s in statuses.list_statuses(db)][::-1]
    statuses.reorder(db, keys)
    assert [s["key"] for s in statuses.list_statuses(db)] == keys
    with pytest.raises(statuses.StatusError):
        statuses.delete_status(db, "new")          # built in
    statuses.set_status(db, "me", "1001", "second_visit")
    with pytest.raises(statuses.StatusError):
        statuses.delete_status(db, "second_visit")  # in use
    statuses.set_status(db, "me", "1001", None)
    statuses.delete_status(db, "second_visit")
