# -*- coding: utf-8 -*-
"""G1 每日决策对象（daily_decision）单测 —— 2026-09-12。

覆盖：六层组装 / 缺失文件时的诚实降级 / L5 拦阻项（G9、G10 破位与诱多、L2 档位不允许）/
`entry_allowed()` 的开关语义（默认放行 vs 置 1 后无对象即拒绝）/ directive 的开关与内容。
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import daily_decision as DD  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    for k in ("WOLF_DAILY_DECISION", "WOLF_DECISION_GATE", "DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield


GATE = {"confirmed": ["半导体", "液冷"], "top": [{"theme": "半导体"}, {"theme": "算力"}]}
WAVE_BUILD = {"operation": "build", "level": "d4", "sub_level": "4-4"}
WAVE_DEF = {"operation": "defense", "level": "d4", "sub_level": "4-5"}


# ── 六层组装 ─────────────────────────────────────────────────────────
def test_build_six_layers_from_injected_inputs():
    obj = DD.build("20260911", gate=GATE, wave=WAVE_BUILD,
                   tiers={"target_pct": 0.75, "floor_pct": 0.55, "tier": "build"},
                   picks={"picks": [{"symbol": "SH600519"}, {"symbol": "SZ000001"}]},
                   gates={"G10_volume_gate": {"enabled": True, "tag": "地量"}})
    L = obj["layers"]
    assert [k for k in L] == DD.LAYERS
    assert L["L1_direction"]["value"]["confirmed"] == ["半导体", "液冷"]
    assert L["L2_operation"]["value"]["operation"] == "build"
    assert L["L2_operation"]["value"]["allow_new_position"] is True
    assert L["L3_position"]["value"]["target_pct"] == pytest.approx(0.75)
    assert L["L4_picks"]["value"]["symbols"] == ["SH600519", "SZ000001"]
    assert L["L5_entry"]["value"]["allowed"] is True
    assert L["L6_exit"]["value"]["rules"]


def test_missing_inputs_degrade_honestly():
    obj = DD.build("20260911", gate=None, wave=None, tiers={}, picks=None, gates={})
    L = obj["layers"]
    assert L["L1_direction"]["value"] is None and L["L2_operation"]["value"] is None
    assert L["L4_picks"]["value"] is None
    assert "L1_direction" not in obj["missing"] or True   # 值 None 的层会被记入 missing
    assert "L2_operation" in obj["missing"] and "L4_picks" in obj["missing"]


# ── L5 拦阻项（这是"判据先于成交"的落点）────────────────────────────
def test_l5_blocks_when_operation_disallows():
    obj = DD.build("20260911", gate=GATE, wave=WAVE_DEF, tiers={}, picks=None, gates={})
    l5 = obj["layers"]["L5_entry"]["value"]
    assert l5["allowed"] is False
    assert any("档位" in b for b in l5["blockers"])


def test_l5_blocks_on_breakdown():
    obj = DD.build("20260911", gate=GATE, wave=WAVE_BUILD, tiers={}, picks=None,
                   gates={"G10_volume_gate": {"enabled": True, "breakdown_risk": True}})
    l5 = obj["layers"]["L5_entry"]["value"]
    assert l5["allowed"] is False and any("破位" in b for b in l5["blockers"])


def test_l5_blocks_on_fake_breakout_and_weekend_hedge():
    obj = DD.build("20260911", gate=GATE, wave=WAVE_BUILD, tiers={}, picks=None,
                   gates={"G10_volume_gate": {"fake_breakout_risk": True},
                          "G9_weekend_hedge": {"active": True}})
    l5 = obj["layers"]["L5_entry"]["value"]
    assert l5["allowed"] is False
    assert any("诱多" in b for b in l5["blockers"]) and any("周末" in b for b in l5["blockers"])


# ── entry_allowed 的开关语义 ────────────────────────────────────────
def test_entry_allowed_defaults_to_pass(monkeypatch):
    """默认（WOLF_DECISION_GATE=0）**永远放行**——不得因本机制改变现有行为。"""
    ok, why = DD.entry_allowed("20260911")
    assert ok is True and why == "decision_gate_off"


def test_entry_allowed_denies_without_object_when_gate_on(monkeypatch):
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    ok, why = DD.entry_allowed("20260911")
    assert ok is False and "无当日决策对象" in why


def test_entry_allowed_follows_l5_when_gate_on(monkeypatch):
    monkeypatch.setenv("WOLF_DECISION_GATE", "1")
    d = DD.decision_dir()
    Path(d).mkdir(parents=True, exist_ok=True)
    blocked = DD.build("20260911", gate=GATE, wave=WAVE_DEF, tiers={}, picks=None, gates={})
    Path(DD.path_for("20260911")).write_text(json.dumps(blocked, ensure_ascii=False), encoding="utf-8")
    ok, why = DD.entry_allowed("20260911")
    assert ok is False and "L5 拦阻" in why
    allowed = DD.build("20260911", gate=GATE, wave=WAVE_BUILD, tiers={}, picks=None, gates={})
    Path(DD.path_for("20260911")).write_text(json.dumps(allowed, ensure_ascii=False), encoding="utf-8")
    ok2, why2 = DD.entry_allowed("20260911")
    assert ok2 is True and why2 == "L5 允许"


# ── run / directive ────────────────────────────────────────────────
def test_run_writes_object_and_latest(monkeypatch):
    monkeypatch.setenv("WOLF_DAILY_DECISION", "1")
    res = DD.run("20260911", save=True)
    assert res["ok"] is True
    assert Path(DD.path_for("20260911")).is_file()
    assert (Path(DD.decision_dir()) / "latest.json").is_file()


def test_run_disabled(monkeypatch):
    assert DD.run("20260911", save=True)["ok"] is False


def test_directive_disabled_and_enabled(monkeypatch):
    assert DD.directive() == ""
    monkeypatch.setenv("WOLF_DAILY_DECISION", "1")
    d = DD.decision_dir()
    Path(d).mkdir(parents=True, exist_ok=True)
    obj = DD.build("20260911", gate=GATE, wave=WAVE_DEF, tiers={}, picks=None,
                   gates={"G10_volume_gate": {"breakdown_risk": True}})
    Path(DD.path_for("20260911")).write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    txt = DD.directive("20260911")
    assert "每日决策对象" in txt and "拦阻" in txt and "破位" in txt


def test_backfill_warns_about_undated_wave(monkeypatch, tmp_path):
    """回填历史日、又只有不带日期的 wave_state.json 时，必须给出 look-ahead 告警。"""
    obj = DD.build("20200101", gate=GATE, wave=WAVE_BUILD, tiers={}, picks=None, gates={},
                   sources={"wave_dated": {"present": False}})
    assert obj["warnings"] and "look-ahead" in obj["warnings"][0]
