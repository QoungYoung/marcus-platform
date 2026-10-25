# -*- coding: utf-8 -*-
"""decision_window.py — 决策/执行时间窗口（给 agent 的 phase 协议）。

原则：
  · 14:50 只预览，不写“已破位”、不卖；
  · 14:55 尾盘确认，可当天执行，但标 tail_confirm（非官方收盘）；
  · 15:00 官方收盘，当天不能成交，动作放 next_day；
  · 13:00-14:30 只预警不执行；
  · intraday 的 today_low/high 只到 cutoff。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

PHASES = ("pre_open", "intraday", "intraday_blocked", "tail_preview",
          "tail_confirm", "close", "post_close")

CLOSE_MODES = ("close_break", "reclaim_failure", "close_threshold")


def _hm(now: datetime) -> str:
    return now.strftime("%H%M")


def phase_at(now: Optional[datetime] = None) -> Dict[str, Any]:
    """返回当前 phase + 是否可当天成交 + 执行窗口。"""
    now = now or datetime.now()
    hm = _hm(now)
    if hm < "0930":
        phase, same, exe = "pre_open", False, []
    elif hm < "1300":
        phase, same, exe = "intraday", True, ["now"]
    elif hm < "1430":
        phase, same, exe = "intraday_blocked", False, ["tail"]
    elif hm < "1445":
        phase, same, exe = "intraday", True, ["now"]
    elif hm < "1455":
        phase, same, exe = "tail_preview", False, ["preview_only"]
    elif hm < "1500":
        phase, same, exe = "tail_confirm", True, ["now", "close_auction"]
    elif hm < "1505":
        phase, same, exe = "close", False, ["next_open", "next_early"]
    else:
        phase, same, exe = "post_close", False, ["next_open", "next_early"]
    return {"phase": phase, "time": now.strftime("%H:%M"), "same_day_executable": same,
            "execution_window": exe, "is_final": phase in ("close", "post_close")}


def window_for_mode(mode: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """动作模式 → 决策/执行窗口。close 类二选一：尾盘代理或官方收盘+次日。"""
    base = phase_at(now)
    mode = (mode or "").strip().lower()
    if mode in CLOSE_MODES:
        return {
            "mode": mode,
            "decision_windows": [
                {"phase": "tail_preview", "time": "14:45-14:55", "ask_agent": False,
                 "action": "preview_only"},
                {"phase": "tail_confirm", "time": "14:55-15:00", "ask_agent": True,
                 "action": "same_day_tail"},
                {"phase": "close", "time": "15:00", "ask_agent": True,
                 "action": "next_day_if_not_tailed"},
            ],
            "current": base,
            "note": "尾盘代理可当天走；官方收盘只能次日，不能混。",
        }
    if mode == "tail_confirm":
        return {"mode": mode, "decision_windows": [{"phase": "tail_confirm", "time": "14:45-15:00",
                "ask_agent": True, "action": "same_day_tail"}], "current": base}
    if mode == "intraday_3x15":
        return {"mode": mode, "decision_windows": [{"phase": "intraday", "time": "break+45min",
                "ask_agent": True, "action": "after_confirm"}], "current": base,
                "note": "G 等反抽；H 确认后当日下调。"}
    if mode == "time_specific":
        return {"mode": mode, "decision_windows": [{"phase": "intraday", "time": "14:30",
                "ask_agent": True, "action": "at_14_30"}], "current": base}
    if mode == "volume_fast":
        return {"mode": mode, "decision_windows": [{"phase": "intraday", "time": "volume_break",
                "ask_agent": True, "action": "immediate"}], "current": base}
    if mode == "rebound_exit":
        return {"mode": mode, "decision_windows": [{"phase": "intraday", "time": "rebound_zone",
                "ask_agent": True, "action": "sell_into_rebound"}], "current": base}
    return {"mode": mode or "unknown", "decision_windows": [], "current": base}


def should_ask_agent(mode: str, phase: str) -> bool:
    """当前 phase 是否应该把点位/动作交给 agent。"""
    mode = (mode or "").strip().lower()
    if phase == "tail_preview":
        return False
    if phase == "intraday_blocked":
        return False
    if mode in CLOSE_MODES:
        return phase in ("tail_confirm", "close")
    if mode == "tail_confirm":
        return phase in ("tail_confirm", "close")
    return phase == "intraday"
