"""Groups move from config.yaml into the database; the scanner reads the selected ones."""
import pytest

import scan_facebook
from apartment_bot import groups


def test_seed_once_from_config(db, cfg):
    assert groups.seed_from_config(db, cfg) == 2
    assert groups.seed_from_config(db, cfg) == 0
    assert groups.selected_ids(db) == ["g1", "g2"]
    assert groups.names(db, cfg)["g1"] == "Group One"


def test_parse_group_id():
    assert groups.parse_group_id("https://www.facebook.com/groups/123456/?ref=share") == "123456"
    assert groups.parse_group_id("telavivroommates") == "telavivroommates"
    assert groups.parse_group_id("not a group!") is None


def test_discovered_groups_are_not_selected_and_keep_selection(db, cfg):
    groups.seed_from_config(db, cfg)
    added, updated = groups.upsert_discovered(db, [
        {"id": "g1", "name": "Group One (new name)", "member_count": 5000},
        {"id": "g9", "name": "Found", "privacy": "private", "is_member": 0},
    ], "search", "דירות")
    assert (added, updated) == (1, 1)
    by_id = {g["id"]: g for g in groups.list_groups(db)}
    assert by_id["g1"]["selected"] == 1 and by_id["g1"]["member_count"] == 5000
    assert by_id["g9"]["selected"] == 0 and by_id["g9"]["search_query"] == "דירות"
    groups.set_selected(db, "g9", True)
    groups.set_selected(db, "g1", False)
    assert sorted(groups.selected_ids(db)) == ["g2", "g9"]


def test_add_manual(db):
    assert groups.add_manual(db, "https://facebook.com/groups/myGroup/") == "myGroup"
    with pytest.raises(ValueError):
        groups.add_manual(db, "??")


def test_scanner_reads_selected_groups(v3_db, cfg, monkeypatch):
    monkeypatch.setattr(scan_facebook, "DB_PATH", v3_db)
    assert scan_facebook.selected_groups(cfg) == ["g1", "g2"]     # seeded from config on first run
    from apartment_bot import db as dbmod
    conn = dbmod.connect(v3_db)
    groups.set_selected(conn, "g1", False)
    conn.close()
    assert scan_facebook.selected_groups(cfg) == ["g2"]
    assert scan_facebook.selected_groups(cfg, ["g1"]) == ["g1"]     # --group may name an unselected group
    with pytest.raises(SystemExit):
        scan_facebook.selected_groups(cfg, ["nope"])
