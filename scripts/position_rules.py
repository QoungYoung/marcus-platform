# -*- coding: utf-8 -*-
"""position_rules.py — 个股高位/低位规则包（只描述，不下单）。

输入 stock_agent_facts 的字段：
  dist_high120 / range_pos_120 / ret60 / is_st / earnings / leader
输出：
  state: high | mid | low
  entry_mode: no_chase | ambush | neutral
  hold_bias: 卖强留弱 / 留强丢弱 / 等确认
  evidence / risk_flags / leader_evidence
阈值来源：config/mainline/position_rules.yaml（初值，待判例校准）。
"""
from __future__ import annotations
import json
import os
import sys
from typing import Any, Dict

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
CFG = os.path.join(ROOT, "config", "mainline", "position_rules.yaml")
DEFAULTS = {
    "low": {"dist_high120_max": -0.30, "range_pos_120_max": 0.35, "ret60_max": 0.0},
    "high": {"dist_high120_min": -0.10, "range_pos_120_min": 0.65, "ret60_min": 0.0, "range_pos_120_hard": 0.80},
}


def load_cfg() -> Dict[str, Any]:
    try:
        return yaml.safe_load(open(CFG, encoding="utf-8")) or {}
    except Exception:
        return {}


def _wave_operation() -> str:
    try:
        from app.services.position_tier import read_wave_state
        return str((read_wave_state() or {}).get("operation") or "side")
    except Exception:
        try:
            return str((json.load(open(os.path.join(ROOT, "data", "wave_state.json"), encoding="utf-8")) or {}).get("operation") or "side")
        except Exception:
            return "side"


def wave_gate(mainline_active: bool | None = None) -> Dict[str, Any]:
    op = _wave_operation()
    low_allowed = False
    if mainline_active:
        try:
            from app.services.position_tier import three_tier_gate
            d_new = three_tier_gate(intent="new_base", mainline_dir=True)
            d_probe = three_tier_gate(intent="probe", mainline_dir=True)
            low_allowed = bool(d_new.get("intent_allowed") or d_probe.get("intent_allowed"))
        except Exception:
            low_allowed = op in ("build", "side", "t_only")
    return {
        "operation": op,
        "mainline_active": bool(mainline_active),
        "low_ambush_allowed": low_allowed,
        "high_action": "reduce" if op in ("defense", "exit") else "no_chase",
        "reason": "主线未 active" if not mainline_active else ("浪型禁止低吸" if not low_allowed else "允许低位候选"),
    }


def classify(c: Dict[str, Any], cfg: Dict[str, Any] | None = None,
             mainline_active: bool | None = None, wave: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = cfg or load_cfg()
    th = cfg.get("thresholds") or DEFAULTS
    low = th.get("low") or DEFAULTS["low"]
    high = th.get("high") or DEFAULTS["high"]
    dh = float(c.get("dist_high120") or 0.0)
    pos = float(c.get("range_pos_120") if c.get("range_pos_120") is not None else 0.5)
    r60 = float(c.get("ret60") or 0.0)
    is_st = bool(c.get("is_st"))
    earn = c.get("earnings") or {}
    est = str(earn.get("status") or "missing")
    risk = []
    if is_st:
        risk.append("ST/退市风险")
    if est == "loss":
        risk.append("业绩亏损")
    elif est == "missing":
        risk.append("业绩缺失")
    leader = c.get("leader") or {}
    if dh <= float(low["dist_high120_max"]) and pos <= float(low["range_pos_120_max"]) and r60 <= float(low["ret60_max"]):
        state = "low"
    elif ((dh >= float(high["dist_high120_min"]) and pos >= float(high["range_pos_120_min"]) and r60 >= float(high["ret60_min"]))
          or pos >= float(high["range_pos_120_hard"])):
        state = "high"
    else:
        state = "mid"
    wave = wave or wave_gate(mainline_active)
    if state == "high":
        entry_mode = "no_chase"
        hold_bias = "卖强减仓" if wave.get("high_action") == "reduce" else "卖强留弱，拉升后走"
    elif state == "low":
        entry_mode, hold_bias = "ambush_candidate", "留强丢弱，老龙头埋伏"
        if not wave.get("mainline_active"):
            entry_mode = "watch"
        elif not wave.get("low_ambush_allowed"):
            entry_mode = "watch"
    else:
        entry_mode, hold_bias = "neutral", "等确认"
    if state == "low" and (is_st or est == "loss"):
        entry_mode = "no_entry"
    evidence = [
        f"dist_high120={dh:.2%}",
        f"range_pos_120={pos:.2f}",
        f"ret60={r60:.2%}",
    ]
    return {
        "state": state,
        "entry_mode": entry_mode,
        "hold_bias": hold_bias,
        "evidence": evidence,
        "risk_flags": risk,
        "leader_evidence": {
            "seed_role": leader.get("seed_role"),
            "top3_days_120_pool": leader.get("top3_days_120_pool"),
            "limit_up_120d": leader.get("limit_up_120d"),
            "pool_size": leader.get("pool_size"),
        },
        "wave": wave,
        "threshold_source": "position_rules.yaml (初值，待判例校准)",
    }
