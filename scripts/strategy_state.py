# -*- coding: utf-8 -*-
"""strategy_state.py — 全局状态机（不是单因子排序）。

输入：
  global: wave_operation / mainline_active / active_sub
  theme:  theme_position (high/mid/low)
  stock:  position (high/mid/low), leader, stabilization, volume, logic
输出：
  state + reasons + missing
先判“能不能做”，再判“低位/高位该用哪套规则”。
"""
from __future__ import annotations
from typing import Any, Dict


def state_of(*, wave_operation: str, mainline_active: bool, theme_position: str,
             stock_position: str, leader: bool, stabilization: bool, volume: bool,
             logic: bool | None = None) -> Dict[str, Any]:
    reasons = []
    missing = []
    op = str(wave_operation or "side").lower()
    if op in ("defense", "exit"):
        return {"state": "no_trade", "reasons": [f"wave={op} 禁止新开"], "missing": missing}
    if not mainline_active:
        return {"state": "watch_mainline", "reasons": ["mainline_active=false"], "missing": missing}
    if theme_position == "high":
        if stock_position == "low":
            return {"state": "high_board_low_stock",
                    "reasons": ["板块高位 + 个股相对低位：只做相对低，不做低位埋伏"],
                    "missing": missing}
        return {"state": "high_logic", "reasons": ["板块高位：卖强留弱/不追"], "missing": missing}
    if stock_position == "high":
        return {"state": "high_risk", "reasons": ["个股高位：不追"], "missing": missing}
    if stock_position == "low":
        if leader and stabilization and volume:
            return {"state": "ambush_confirmed",
                    "reasons": ["低位 + 老龙头 + 不再创新低/企稳 + 量能确认"],
                    "missing": missing}
        if leader:
            return {"state": "watch_low_leader",
                    "reasons": ["低位 + 老龙头，但缺企稳/量能"], "missing": missing}
        return {"state": "watch_low", "reasons": ["低位但缺老龙头"], "missing": missing}
    return {"state": "watch_mid", "reasons": ["中位：等确认"], "missing": missing}
