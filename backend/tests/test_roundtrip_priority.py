# -*- coding: utf-8 -*-
"""A1：等量换手腿的「目标优先 + 破黄线确认」单测 —— 2026-09-14。

狼大 2026-08-04 10:48「在这个半小时内有个绝对不能破的点 就是日均线那条黄线，**一旦突发跌破直接走**。
**如果没跌破就找这半小时的高点**。」2026-08-13 楼275/280「至少能有吃 3-5 个点的幅度吧」。
背景：真 m5 验收显示"破线"在 33/33 条腿上都会发生（不是择时信号）→ 目标优先、破线需确认。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def _fresh(monkeypatch):
    import app.services.roundtrip_priority as RP
    return importlib.reload(RP)


def test_target_priority_over_vwap_break(monkeypatch):
    """A1 核心：**达标优先** —— 即便同时"在黄线下"，只要到 +3% 就按达标卖（他"没跌破就找高点"的对称）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.delenv("WOLF_RT_PRIORITY", raising=False)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0.005")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "2")
    # 即便"破线已确认"（cur 比黄线低 1.7%），只要到 +3% → 按**达标**卖（A1 的行为改变）
    act, why, streak = RP.roundtrip_decision(1.032, 1.000, 1.05, 0.03, streak=0)
    assert act == "sell_target" and "兑现幅度" in why


def test_vwap_break_needs_confirmation_by_pct(monkeypatch):
    """「**突发**跌破」= 幅度确认：贴着线蹭一下（<0.5%）不算。"""
    RP = _fresh(monkeypatch)
    monkeypatch.delenv("WOLF_RT_PRIORITY", raising=False)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0.005")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "2")
    act, why, streak = RP.roundtrip_decision(0.995, 1.000, 0.997, 0.03, streak=0)   # 破线 0.2%
    assert act == "wait" and streak == 1
    act, why, streak = RP.roundtrip_decision(0.986, 1.000, 0.995, 0.03, streak=0)   # 破线 0.9% → 突发
    assert act == "sell_vwap" and "突发跌破" in why


def test_vwap_break_needs_confirmation_by_rounds(monkeypatch):
    """「突发跌破」= 持续确认：连续 2 轮在黄线下才走（防插针）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.delenv("WOLF_RT_PRIORITY", raising=False)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "2")
    act, why, s1 = RP.roundtrip_decision(0.999, 1.000, 1.000, 0.03, streak=0)
    assert act == "wait" and s1 == 1
    act, why, s2 = RP.roundtrip_decision(0.999, 1.000, 1.000, 0.03, streak=s1)
    assert act == "sell_vwap" and "连续2轮" in why
    # 回到线上 → streak 清零
    act, why, s3 = RP.roundtrip_decision(1.001, 1.000, 1.000, 0.03, streak=s2)
    assert act == "wait" and s3 == 0


def test_rollback_switch_vwap_first(monkeypatch):
    """回退开关：WOLF_RT_PRIORITY=vwap_first 回到旧行为（破线优先）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.setenv("WOLF_RT_PRIORITY", "vwap_first")
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0.005")
    act, why, _ = RP.roundtrip_decision(1.032, 1.000, 1.05, 0.03, streak=0)   # 既达标又确认破线
    assert act == "sell_vwap" and "vwap_first" in why
    monkeypatch.delenv("WOLF_RT_PRIORITY", raising=False)


def test_one_break_exit_when_confirmations_off(monkeypatch):
    """把幅度=0、轮数=1 即"一破就走"（旧语义的可配版本）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.delenv("WOLF_RT_PRIORITY", raising=False)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "1")
    act, _, _ = RP.roundtrip_decision(0.9999, 1.000, 1.000, 0.03, streak=0)
    assert act == "sell_vwap"
    monkeypatch.delenv("WOLF_VWAP_BREAK_PCT", raising=False)
    monkeypatch.delenv("WOLF_VWAP_BREAK_ROUNDS", raising=False)


def test_monitor_wiring_and_state():
    import inspect
    from app.services import t_monitor as TM
    src = inspect.getsource(TM.TMonitor._check_roundtrip_sell)
    assert "roundtrip_priority" in src and "roundtrip_decision" in src
    assert "_vwap_break_streak" in inspect.getsource(TM.TMonitor.__init__) or \
           "_vwap_break_streak" in inspect.getsource(TM.TMonitor)
