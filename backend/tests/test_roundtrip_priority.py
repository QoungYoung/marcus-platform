# -*- coding: utf-8 -*-
"""A1+A4：等量换手腿的「时间窗 + 2 点决断 + 目标优先 + 破黄线确认」单测 —— 2026-09-14。

A1 依据：狼大 2026-08-04 10:48「在这个半小时内有个绝对不能破的点 就是日均线那条黄线，
**一旦突发跌破直接走**。**如果没跌破就找这半小时的高点**。」
A4 依据：2025-04-15 成文流程条件 2「**当日只做上午 9:45–10:00、下午 14:00–14:30 这两个时间段**」；
2026-09-02 14:03「**2 点到了 力度不够 我先把早上博弈的先T了** 今天目标3%-4% 实际2% 结束今天半导体做T操作」。
背景：真 m5 验收（n=428）显示"破线"不是择时信号（428/428 必破）→ 目标优先、破线需确认、只作保护。
"""
import importlib
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for _d in [REPO_ROOT / "backend", REPO_ROOT / "apps" / "main_line"]:
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

W_IN = "14:05"      # 第二窗口内
W_IN2 = "09:50"     # 第一窗口内
W_OUT = "10:30"     # 两窗之间


def _fresh(monkeypatch):
    import app.services.roundtrip_priority as RP
    for k in ("WOLF_RT_WINDOW", "WOLF_RT_WINDOWS", "WOLF_RT_FORCE_HM", "WOLF_RT_TIMEOUT_TOL"):
        monkeypatch.delenv(k, raising=False)
    return importlib.reload(RP)


def test_target_priority_over_vwap_break(monkeypatch):
    """A1 核心：**达标优先** —— 即便同时"在黄线下"，只要到 +3% 就按达标卖。"""
    RP = _fresh(monkeypatch)
    monkeypatch.delenv("WOLF_RT_PRIORITY", raising=False)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0.005")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "2")
    act, why, _ = RP.roundtrip_decision(1.032, 1.000, 1.05, 0.03, streak=0, hm=W_IN)
    assert act == "sell_target" and "兑现幅度" in why


def test_vwap_break_needs_confirmation_by_pct(monkeypatch):
    """「**突发**跌破」= 幅度确认：贴着线蹭一下（<0.5%）不算。"""
    RP = _fresh(monkeypatch)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0.005")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "2")
    act, why, streak = RP.roundtrip_decision(0.995, 1.000, 0.997, 0.03, streak=0, hm=W_OUT)
    assert act == "wait" and streak == 1
    act, why, streak = RP.roundtrip_decision(0.986, 1.000, 0.995, 0.03, streak=0, hm=W_OUT)
    assert act == "sell_vwap" and "突发跌破" in why


def test_vwap_break_needs_confirmation_by_rounds(monkeypatch):
    """「突发跌破」= 持续确认：连续 2 轮在黄线下才走（防插针）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "2")
    monkeypatch.setenv("WOLF_RT_FORCE_HM", "off")          # 只测破线，不让到点决断插进来
    act, why, s1 = RP.roundtrip_decision(0.999, 1.000, 1.000, 0.03, streak=0, hm=W_OUT)
    assert act == "wait" and s1 == 1
    act, why, s2 = RP.roundtrip_decision(0.999, 1.000, 1.000, 0.03, streak=s1, hm=W_OUT)
    assert act == "sell_vwap" and "连续2轮" in why
    act, why, s3 = RP.roundtrip_decision(1.001, 1.000, 1.000, 0.03, streak=0, hm=W_OUT)
    assert act == "wait" and s3 == 0


def test_rollback_switch_vwap_first(monkeypatch):
    """回退开关：WOLF_RT_PRIORITY=vwap_first 回到旧行为（破线优先，且不受时间窗约束）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.setenv("WOLF_RT_PRIORITY", "vwap_first")
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0.005")
    act, why, _ = RP.roundtrip_decision(1.032, 1.000, 1.05, 0.03, streak=0, hm=W_OUT)
    assert act == "sell_vwap" and "vwap_first" in why
    monkeypatch.delenv("WOLF_RT_PRIORITY", raising=False)


def test_one_break_exit_when_confirmations_off(monkeypatch):
    """把幅度=0、轮数=1 即"一破就走"（旧语义的可配版本）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0")
    monkeypatch.setenv("WOLF_VWAP_BREAK_ROUNDS", "1")
    monkeypatch.setenv("WOLF_RT_FORCE_HM", "off")
    act, _, _ = RP.roundtrip_decision(0.9999, 1.000, 1.000, 0.03, streak=0, hm=W_OUT)
    assert act == "sell_vwap"


# ── A4：做 T 时间窗 + 2 点决断 ─────────────────────────────────────────

def test_a4_target_only_inside_windows(monkeypatch):
    """A4-1：达标但**不在**做 T 窗口 → 不卖（让他跑），到窗口再兑现。"""
    RP = _fresh(monkeypatch)
    act, why, _ = RP.roundtrip_decision(1.05, 1.000, 1.01, 0.03, streak=0, hm=W_OUT)
    assert act == "wait" and "不在做T窗口" in why
    for hm in (W_IN2, W_IN):
        act, _, _ = RP.roundtrip_decision(1.032, 1.000, 1.01, 0.03, streak=0, hm=hm)
        assert act == "sell_target"


def test_a4_timebox_at_force_time(monkeypatch):
    """A4-2：到 14:00 仍未达标 → 也 T 掉收工（他 2026-09-02「力度不够先T了」）。"""
    RP = _fresh(monkeypatch)
    act, why, _ = RP.roundtrip_decision(1.011, 1.000, 1.005, 0.03, streak=0, hm="14:00")
    assert act == "sell_timebox" and "2026-09-02" in why
    act, _, _ = RP.roundtrip_decision(1.011, 1.000, 1.005, 0.03, streak=0, hm="13:55")   # 未到点
    assert act == "wait"


def test_a4_timebox_tolerance(monkeypatch):
    """A4-3："挣不到就亏个手续费出" —— 容忍小幅浮亏，但**不**变成隐藏止损。"""
    RP = _fresh(monkeypatch)
    act, why, _ = RP.roundtrip_decision(0.997, 1.000, 1.000, 0.03, streak=0, hm=W_IN)   # −0.3%
    assert act == "sell_timebox"
    act, why, _ = RP.roundtrip_decision(0.97, 1.000, 0.96, 0.03, streak=0, hm=W_IN)     # −3%、但在黄线上方（不触发保护）
    assert act == "wait" and "超过容忍" in why
    monkeypatch.setenv("WOLF_RT_TIMEOUT_TOL", "0.02")                                    # 放宽到 2%
    RP2 = importlib.reload(RP)
    act, _, _ = RP2.roundtrip_decision(0.99, 1.000, 0.98, 0.03, streak=0, hm=W_IN)
    assert act == "sell_timebox"


def test_a4_protection_beats_timebox(monkeypatch):
    """A4-4：破线确认（保护）优先于到点收工 —— 理由是"直接走"更硬。"""
    RP = _fresh(monkeypatch)
    RP2 = RP
    monkeypatch.setenv("WOLF_VWAP_BREAK_PCT", "0.005")
    act, why, _ = RP2.roundtrip_decision(0.98, 1.000, 0.995, 0.03, streak=0, hm=W_IN)
    assert act == "sell_vwap" and "突发跌破" in why


def test_a4_window_switch_off_restores_a1(monkeypatch):
    """A4-5：`WOLF_RT_WINDOW=0` → 回 A1（任何时间达标即卖、不做到点收工）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.setenv("WOLF_RT_WINDOW", "0")
    RP2 = importlib.reload(RP)
    act, _, _ = RP2.roundtrip_decision(1.032, 1.000, 1.01, 0.03, streak=0, hm=W_OUT)
    assert act == "sell_target"
    act, _, _ = RP2.roundtrip_decision(1.011, 1.000, 1.005, 0.03, streak=0, hm="14:30")
    assert act == "wait"          # 到点收工也一起关掉


def test_a4_custom_windows_and_force_off(monkeypatch):
    """A4-6：窗口/到点时刻可配；`WOLF_RT_FORCE_HM=off` 只保留窗口语义（A4c）。"""
    RP = _fresh(monkeypatch)
    monkeypatch.setenv("WOLF_RT_WINDOWS", "10:00-10:30")
    monkeypatch.setenv("WOLF_RT_FORCE_HM", "off")
    RP2 = importlib.reload(RP)
    assert RP2.windows() == [(600, 630)]
    act, _, _ = RP2.roundtrip_decision(1.032, 1.000, 1.01, 0.03, streak=0, hm="10:15")
    assert act == "sell_target"
    act, why2, _ = RP2.roundtrip_decision(1.011, 1.000, 1.005, 0.03, streak=0, hm="14:05")
    assert act == "wait"                                # 到点收工已关；不达标也不卖


def test_monitor_wiring_and_state():
    import inspect
    from app.services import t_monitor as TM
    src = inspect.getsource(TM.TMonitor._check_roundtrip_sell)
    assert "roundtrip_priority" in src and "roundtrip_decision" in src
    assert "hm=hm" in src                       # A4：把当前时刻传进去
    assert "_vwap_break_streak" in inspect.getsource(TM.TMonitor)
