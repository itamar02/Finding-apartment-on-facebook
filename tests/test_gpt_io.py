"""Export to / import from an external AI: validation, preview, apply."""
import json

from apartment_bot import gpt_io, human, statuses


def test_export_skips_my_statuses_and_rejections(db, cfg):
    statuses.set_status(db, "me", "1001", "interested")          # set by me -> left out
    payload = gpt_io.export(db, cfg, cfg["users"][0])
    ids = [p["post_id"] for p in payload["posts"]]
    assert ids == ["1002"]          # 1003 is REJECT (and rejected by me), 1004 is old
    assert "needs_details" in payload["prompt"] and "Return ONLY a JSON array" in payload["prompt"]
    assert gpt_io.as_text(payload).endswith("]")
    assert gpt_io.export(db, cfg, cfg["users"][0], include_old=True)["count"] == 2   # + old 1004 (not analyzed)


def test_parse_reply_tolerates_chatter_and_fences():
    reply = 'Sure! Here you go:\n```json\n[{"post_id": "1", "status": "new"}]\n```\nGood luck'
    assert gpt_io.parse_reply(reply) == [{"post_id": "1", "status": "new"}]


def test_preview_validates(db):
    statuses.set_status(db, "me", "1001", "interested")
    reply = json.dumps([
        {"post_id": "1002", "status": "contacted", "note": "לשאול על חניה"},
        {"post_id": "1001", "status": "top_candidate"},             # would replace my status
        {"post_id": "9999", "status": "new"},                        # unknown post
        {"post_id": "1003", "status": "bogus"},                      # unknown status
        {"post_id": "1002", "status": "new"},                        # duplicate
        {"post_id": "1004", "status": "לא רלוונטי"},                 # label instead of key: accepted
    ], ensure_ascii=False)
    p = gpt_io.preview(db, "me", reply)
    assert [c["post_id"] for c in p["changes"]] == ["1002", "1004"]
    assert p["changes"][1]["status"] == "not_relevant"
    assert [c["post_id"] for c in p["conflicts"]] == ["1001"]
    assert len(p["errors"]) == 3


def test_preview_bad_json(db):
    assert gpt_io.preview(db, "me", "no json here")["errors"]


def test_apply_uses_source_gpt_and_keeps_mine(db):
    statuses.set_status(db, "me", "1001", "interested")
    reply = json.dumps([{"post_id": "1002", "status": "not_relevant", "note": "רחוק"},
                        {"post_id": "1001", "status": "taken"}])
    r = gpt_io.apply(db, "me", reply)
    assert r == {"applied": 1, "skipped": 1, "errors": []}
    cur = statuses.current(db, "me", "1002")
    assert cur["source"] == "gpt" and cur["status"] == "not_relevant"
    assert "1002" not in human.rejected(db, "me")        # GPT's opinion is not my rejection
    assert db.execute("SELECT 1 FROM post_seen WHERE post_id = '1002'").fetchone()   # counts as seen
    assert statuses.current(db, "me", "1001")["status"] == "interested"
    r = gpt_io.apply(db, "me", reply, override_human=True)
    assert statuses.current(db, "me", "1001")["status"] == "taken"
    assert db.execute("SELECT count(*) FROM gpt_imports").fetchone()[0] == 2


def test_reapply_is_unchanged(db):
    reply = json.dumps([{"post_id": "1002", "status": "interested"}])
    gpt_io.apply(db, "me", reply)
    assert gpt_io.preview(db, "me", reply)["unchanged"] == 1
