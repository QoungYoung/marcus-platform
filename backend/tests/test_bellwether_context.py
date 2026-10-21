from app.services.wolf_card.bellwether_context import permissions_for_state, all_context, for_subtheme
import json, tempfile, os


def test_alive_allows_second_line_and_high_cut_low():
    p = permissions_for_state("alive")
    assert p["second_line_new"] is True
    assert p["high_cut_low"] is True
    assert p["hold_second_line"] is True
    assert p["reduce_theme"] is False


def test_warning_blocks_new_but_allows_hold():
    p = permissions_for_state("warning")
    assert p["second_line_new"] is False
    assert p["high_cut_low"] is False
    assert p["hold_second_line"] is True


def test_dead_blocks_all_and_reduces():
    p = permissions_for_state("dead")
    assert p["second_line_new"] is False
    assert p["high_cut_low"] is False
    assert p["hold_second_line"] is False
    assert p["reduce_theme"] is True


def test_rotated_is_not_dead_and_carries_successor():
    p = permissions_for_state("rotated", "600000.SH")
    assert p["second_line_new"] is True
    assert p["high_cut_low"] is True
    assert p["successor"] == "600000.SH"


def test_unknown_is_safe_default():
    p = permissions_for_state("nope")
    assert p["second_line_new"] is False
    assert p["high_cut_low"] is False
    assert p["risk"] == "unknown"


def test_loader_by_subtheme(tmp_path):
    f = tmp_path / "state.json"
    json.dump({"date": "20261020", "items": [
        {"subtheme": "AI芯片", "state": "dead", "bellwether_code": "688256.SH"},
        {"subtheme": "光模块/CPO", "state": "rotated", "successor_code": "300308.SZ"},
    ]}, open(f, "w"))
    ctx = all_context(path=str(f))
    assert ctx["AI芯片"]["permissions"]["reduce_theme"] is True
    assert ctx["光模块/CPO"]["permissions"]["successor"] == "300308.SZ"
    assert for_subtheme("不存在", path=str(f))["state"] == "unknown"


def test_stale_does_not_allow_new_or_dead():
    p = permissions_for_state("stale")
    assert p["second_line_new"] is False
    assert p["high_cut_low"] is False
    assert p["hold_second_line"] is True
    assert p["risk"] == "unknown"
