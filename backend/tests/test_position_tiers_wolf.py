# -*- coding: utf-8 -*-
"""仓位分档 + 下限（2026-09-11「把仓位落实」）单测。

狼大原话（**此前漏检的决定性一条**）：
  2026-01-17 答「分享一下自己的仓位策略吗，比如什么时候50%，什么时候75%，什么时候打满」
  「**主升趋势就75%以上** 然后盘中满仓滚动啊 **调整就50%** **有风险就30%** **下跌趋势就不做**」
  2025-08-11「这个位置 **仓位低于55%** 日内分时低于80%都是不太合适」

设计要点：
  · `tier_targets` 按本仓 operation 语义映射（build=主升 / side·t_only=调整 / defense=有风险 /
    exit=下跌不做）—— 映射**属外推**（他的轴是"行情状态"），可 `tier_enabled=false` 关。
  · 原 `total_max_pct=70` 的依据（2026-03-06「现在就是70%仓位」）经复核是**状态描述、不是上限规则**，
    且与"主升75%以上…盘中满仓滚动"直接冲突 → 默认 0=不再额外限制，由分档取代。
  · 建议层（position_cap）与硬拦层（P3 现金底线）**同源**：P3 cash_floor = 100 − 分档目标。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import pytest  # noqa: E402

from app.services import position_tier as PT  # noqa: E402
from app.services import wolf_discipline as WD  # noqa: E402

OPS = ("build", "t_only", "side", "defense", "exit")
EXPECT_TARGET = {"build": 75.0, "t_only": 50.0, "side": 50.0, "defense": 30.0, "exit": 50.0}
EXPECT_FLOOR = {"build": 55.0}


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # 配置已落库 + 有 TTL 缓存：单测必须①关掉 DB 读（本机无 PG，否则每例卡连接超时）
    # ②每次清缓存，否则上一个用例的配置会串到下一个（曾导致 3 个用例假失败）
    monkeypatch.setenv("WOLF_DISCIPLINE_CFG_DB", "0")
    for k in ("WOLF_POSITION_TIER_OP", "P3_USE_TIER_TARGETS"):
        monkeypatch.delenv(k, raising=False)
    WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})
    WD._CFG_DB_BREAK.update({"until": 0.0, "fails": 0, "warned": False})
    yield
    WD._CFG_CACHE.update({"at": 0.0, "cfg": None, "src": ""})


def pf(ratio_pct, total=100000.0):
    """按目标持仓占比造一个 portfolio。"""
    pos = total * ratio_pct / 100.0
    return {"cash": total - pos, "total_asset": total,
            "positions": [{"symbol": "SH600000", "market_value": pos}] if pos > 0 else []}


# ── 分档表（狼大 2026-01-17） ──

class TestTierTable:
    def test_targets_match_wolf(self):
        for op, v in EXPECT_TARGET.items():
            assert WD.tier_target_pct(op) == v, op

    def test_floor_only_on_build(self):
        for op in OPS:
            assert WD.tier_floor_pct(op) == EXPECT_FLOOR.get(op), op

    def test_no_tier_has_floor_above_target(self):
        """内部一致性：任何档都不允许 下限 > 目标（否则自相矛盾）。"""
        for op in OPS:
            f = WD.tier_floor_pct(op) or 0
            t = WD.tier_target_pct(op) or 0
            assert f <= t, f"{op}: 下限{f} > 目标{t}"

    def test_unknown_operation_returns_none(self):
        assert WD.tier_target_pct("nonsense") is None
        assert WD.tier_floor_pct("nonsense") is None

    def test_disabled_returns_none(self, tmp_path):
        (tmp_path / "wolf_discipline.json").write_text(
            json.dumps({"position_cap": {"tier_enabled": False}}), encoding="utf-8")
        assert WD.tier_target_pct("build") is None


# ── 建议层 position_cap ──

class TestPositionCapTier:
    def test_within_target_allowed(self):
        r = WD.position_cap(pf(40), operation="t_only")
        assert r["allowed"] is True and r["target_pct"] == 50.0

    def test_above_target_blocks(self):
        r = WD.position_cap(pf(65), operation="t_only")     # 调整档目标 50%
        assert r["allowed"] is False
        assert "分档(t_only档)目标50%" in r["reason"]
        assert "2026-01-17" in r["directive"]

    def test_defense_is_strictest(self):
        assert WD.position_cap(pf(50), operation="defense")["allowed"] is False
        assert WD.position_cap(pf(25), operation="defense")["allowed"] is True

    def test_build_allows_up_to_75(self):
        """主升档允许到 75% —— 这正是原来 70% 死上限会压制的地方。"""
        assert WD.position_cap(pf(72), operation="build")["allowed"] is True
        assert WD.position_cap(pf(80), operation="build")["allowed"] is False

    def test_exit_tier_does_not_force_zero(self):
        """exit 档不把总仓位压到 0（该档 P3 已是 reduce_only，且他确实做 T 回补）。"""
        assert WD.tier_target_pct("exit") == 50.0

    def test_below_floor_is_advice_not_block(self):
        r = WD.position_cap(pf(30), operation="build")
        assert r["allowed"] is True and r["below_floor"] is True
        assert "2025-08-11" in r["directive"] and "过度减仓" in r["directive"]

    def test_floor_not_applied_to_adjustment_tier(self):
        """55% 下限只挂 build 档：调整档 50% 目标下，40% 持仓不该被判"偏低"。"""
        r = WD.position_cap(pf(40), operation="t_only")
        assert r["below_floor"] is False and r["directive"] == ""

    def test_operation_env_override(self, monkeypatch):
        monkeypatch.setenv("WOLF_POSITION_TIER_OP", "defense")
        assert WD.position_cap(pf(50))["allowed"] is False

    def test_operation_from_wave_state(self, tmp_path):
        (tmp_path / "wave_state.json").write_text(
            json.dumps({"operation": "defense"}), encoding="utf-8")
        assert WD.position_cap(pf(50))["allowed"] is False

    def test_fallback_to_total_max_when_tier_disabled(self, tmp_path):
        (tmp_path / "wolf_discipline.json").write_text(
            json.dumps({"position_cap": {"enabled": True, "tier_enabled": False,
                                         "total_max_pct": 70.0}}), encoding="utf-8")
        r = WD.position_cap(pf(72), operation="build")
        assert r["allowed"] is False and "total_max_pct" in r["reason"]

    def test_disabled_rule_allows_all(self, tmp_path):
        (tmp_path / "wolf_discipline.json").write_text(
            json.dumps({"position_cap": {"enabled": False}}), encoding="utf-8")
        assert WD.position_cap(pf(99), operation="defense")["allowed"] is True


# ── 硬拦层 P3 与建议层同源 ──

class TestP3SharesTierSource:
    def test_cash_floor_derived_from_tier(self):
        for op in OPS:
            t = WD.tier_target_pct(op)
            assert PT._tier_cash_floor(op, 999) == round(100.0 - t, 1), op

    def test_rollback_switch_uses_config_value(self, monkeypatch):
        monkeypatch.setenv("P3_USE_TIER_TARGETS", "0")
        assert PT._tier_cash_floor("build", 25) == 25.0

    def test_gate_reports_tier_derived_floor(self):
        # 注意: three_tier_gate 的 wave_state 走 read_wave_state()（读 workspace/data/wave_state.json），
        # 与 wolf_discipline 的 DATA_DIR 不是同一个源 → 测试里显式注入，避免依赖文件布局
        dec = PT.three_tier_gate(intent="t_refill", has_base=True,
                                 wave_state={"operation": "defense", "level": "d4", "sub_level": "4-2"})
        assert dec["cash_floor_pct"] == 70.0          # 100 − 30（有风险档）

    def test_gate_cash_floor_matches_cap_for_same_op(self):
        """同源校验：同一个 operation 下，硬拦侧的现金底线必须 = 100 − 建议层目标。"""
        for op in OPS:
            dec = PT.three_tier_gate(intent="new_base", has_base=True,
                                     wave_state={"operation": op, "level": "d3", "sub_level": "3-3"})
            assert dec["cash_floor_pct"] == round(100.0 - EXPECT_TARGET[op], 1), op

    def test_display_names_are_business_words(self):
        dec = PT.three_tier_gate(intent="probe", has_base=True,
                                 wave_state={"operation": "build", "level": "d3", "sub_level": "3-3"})
        assert dec["tier"] in ("试探底仓(自设档,狼大无语料)", "REJECT")
