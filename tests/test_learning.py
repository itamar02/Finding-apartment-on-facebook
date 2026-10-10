"""Editing config.yaml (comments kept, backup, validation) and the learning loop."""
import json
import shutil

import pytest

from apartment_bot import config_edit, learning, statuses
from apartment_bot.config import load_config

from .conftest import ROOT


@pytest.fixture
def cfg_file(tmp_path):
    p = tmp_path / "config.yaml"
    shutil.copy(ROOT / "config.example.yaml", p)
    return p


def test_apply_keeps_comments_and_backs_up(cfg_file):
    before = cfg_file.read_text(encoding="utf-8")
    new_cfg, backup = config_edit.apply([("features.dealbreakers", "add", "קומת קרקע"),
                                         ("budget.preferred_max_rent", "set", "5500"),
                                         ("move_in.earliest", "set", "2026-11-01")], "me", path=cfg_file)
    after = cfg_file.read_text(encoding="utf-8")
    assert backup.read_text(encoding="utf-8") == before
    assert "# EDIT: used for roommate age requirements" in after          # comments survive
    u = new_cfg["users"][0]
    assert u["features"]["dealbreakers"] == ["קומת קרקע"] and u["budget"]["preferred_max_rent"] == 5500
    assert load_config(cfg_file)["users"][0]["move_in"]["earliest"] == "2026-11-01"
    config_edit.apply([("features.dealbreakers", "remove", "קומת קרקע")], "me", path=cfg_file)
    assert load_config(cfg_file)["users"][0]["features"]["dealbreakers"] == []


def test_invalid_changes_leave_the_file_alone(cfg_file):
    before = cfg_file.read_text(encoding="utf-8")
    for change in [("budget.preferred_max_rent", "set", "99999"),        # above the absolute max
                   ("broker.policy", "set", "sometimes"),
                   ("app.api_key", "set", "x"),                          # not editable
                   ("listing.allowed_types", "set", ["castle"])]:
        with pytest.raises(config_edit.EditError):
            config_edit.apply([change], "me", path=cfg_file)
    assert cfg_file.read_text(encoding="utf-8") == before
    assert not list(cfg_file.parent.glob("config.backup-*"))


class FakeRouter:
    def __init__(self, proposals):
        self.proposals = proposals

    def json_call(self, stage, system, content, schema):
        assert stage == "learning"
        return json.dumps({"proposals": self.proposals}), {}, 0.0, "fake"


def _reject_many(db, n=4):
    ids = []
    for i in range(n):
        pid = f"90{i}"
        db.execute("INSERT INTO posts (post_id, group_id, text, url, first_seen_at) VALUES (?, 'g1', 'קומת קרקע', 'u', 'x')",
                   (pid,))
        db.commit()
        statuses.set_status(db, "me", pid, "not_relevant", "קומת קרקע")
        ids.append(pid)
    return ids


def test_learning_keeps_only_supported_valid_new_proposals(db, cfg, cfg_file):
    ids = _reject_many(db)
    router = FakeRouter([
        {"path": "features.dealbreakers", "op": "add", "value": "קומת קרקע", "reason": "דחית 4 דירות בקומת קרקע",
         "evidence_post_ids": ids},
        {"path": "features.dealbreakers", "op": "add", "value": "מרתף", "reason": "x", "evidence_post_ids": ids[:1]},
        {"path": "app.ai_model", "op": "set", "value": "x", "reason": "x", "evidence_post_ids": ids},
        {"path": "budget.absolute_max_rent", "op": "add", "value": "1", "reason": "x", "evidence_post_ids": ids},
        {"path": "features.nice_to_have", "op": "add", "value": "balcony", "reason": "x", "evidence_post_ids": ids},
    ])
    cfg["users"][0]["features"]["nice_to_have"] = ["balcony"]
    assert learning.run(db, cfg, "me", router) == 1
    props = learning.proposals(db, "me")
    assert props[0]["value"] == "קומת קרקע" and props[0]["evidence"] == ids
    assert learning.run(db, cfg, "me", router) == 0               # not proposed twice
    new_cfg = learning.decide(db, "me", props[0]["id"], True, config_path=cfg_file)
    assert new_cfg["users"][0]["features"]["dealbreakers"] == ["קומת קרקע"]
    assert learning.proposals(db, "me") == []
    with pytest.raises(config_edit.EditError):
        learning.decide(db, "me", props[0]["id"], True, config_path=cfg_file)


def test_learning_needs_enough_reactions_and_schedule(db, cfg):
    assert learning.run(db, cfg, "me", FakeRouter([])) == 0
    assert not learning.due(db)                                   # default: on demand
    from apartment_bot import settings
    settings.put(db, {"learning": {"mode": "weekly", "last_run": None}})
    assert learning.due(db)
    learning._mark_run(db)
    assert not learning.due(db)
