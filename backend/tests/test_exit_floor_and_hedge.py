# -*- coding: utf-8 -*-
"""退出层 2026-09-14 三项改动的定向单测（用户拍板：C1 floor 穿透 / C2 避险执行 / G0 死腿清理）。

覆盖：
  · 底仓穿透的**机制白名单**（只有"顶部阶段完全止盈"允许卖底仓 —— 狼大 2025-05-13）；
  · 周末避险的卖出量 = **T 仓的一半**（狼大 2026-08-21「把这两天T进去的仓位出来一半」）；
  · 避险执行门（active ∧ 当日 ∧ 已过 14:30）；
  · 死腿过滤（custom_trail_sell 不再跨日结转）；
  · 纪律类卖腿的账户口径改成 stock + t（t 是测试账户）。
"""
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

from app.services import t_monitor as M  # noqa: E402


def test_floor_break_whitelist(monkeypatch):
    monkeypatch.delenv("WOLF_FLOOR_BREAK", raising=False)
    # 他 2025-05-13「顶部阶段…全止盈」→ 中轨全止盈可以卖底仓
    assert M._floor_break_enabled("wolf_boll_mid_exit") is True
    # 其它兑现腿仍"底仓不动"（2025-05-27）
    for k in ("custom_vwap_sell", "high_sell", "custom_support_sell", "wolf_profit_take_sell",
              "wolf_board_half_sell", "wolf_weekend_hedge_sell"):
        assert M._floor_break_enabled(k) is False
    # 整体开关可退回
    monkeypatch.setenv("WOLF_FLOOR_BREAK", "0")
    assert M._floor_break_enabled("wolf_boll_mid_exit") is False


def test_hedge_reduce_volume_half_of_t_space():
    assert M._hedge_reduce_volume(20000, 10000) == 5000     # T 仓 10000 → 减半
    assert M._hedge_reduce_volume(10000, 10000) == 0        # 无 T 仓 → 不卖底仓
    assert M._hedge_reduce_volume(1000, 0) == 500           # 100 股整数倍
    assert M._hedge_reduce_volume(250, 0) == 100
    assert M._hedge_reduce_volume(0, 0) == 0


def test_wh_execute_gate():
    ok = {"active": True, "as_of": "20260914"}
    assert M._wh_should_execute(ok, "20260914", "1430") is True
    assert M._wh_should_execute(ok, "20260914", "1429") is False    # 未到 2 点半
    assert M._wh_should_execute(ok, "20260915", "1500") is False    # 隔日状态不算
    assert M._wh_should_execute({"active": False, "as_of": "20260914"}, "20260914", "1500") is False
    assert M._wh_should_execute(None, "20260914", "1500") is False


def test_wh_exec_switch(monkeypatch):
    monkeypatch.delenv("WOLF_WH_EXEC", raising=False)
    assert M._wh_exec_enabled() is True          # 用户拍板：直接执行（不再影子）
    monkeypatch.setenv("WOLF_WH_EXEC", "0")
    assert M._wh_exec_enabled() is False


def test_dead_kind_not_rolled():
    conds = [{"trigger_kind": "custom_vwap_sell", "symbol": "SH600000"},
             {"trigger_kind": "custom_trail_sell", "symbol": "SH600000"},
             {"trigger_kind": "high_sell", "symbol": "SZ000001"},
             {"trigger_kind": "wolf_boll_mid_exit", "symbol": "SH588170"}]
    out = [c["trigger_kind"] for c in M._rollable(conds)]
    assert "custom_trail_sell" not in out
    assert out == ["custom_vwap_sell", "high_sell", "wolf_boll_mid_exit"]
    assert "custom_trail_sell" in M._ROLL_SKIP_KINDS


def test_discipline_positions_covers_both_accounts():
    """纪律持仓必须是 stock + t 两个账户（t 是测试账户，主账户是 stock）。"""
    import inspect
    src = inspect.getsource(M.TMonitor._discipline_positions)
    assert '"stock"' in src and '"t"' in src
    for fn in ("_check_weekend_hedge", "_check_profit_take", "_check_board_half", "_check_boll_mid_exit"):
        assert hasattr(M.TMonitor, fn), fn
