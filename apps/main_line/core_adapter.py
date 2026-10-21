# -*- coding: utf-8 -*-
"""core_adapter.py — 趋势底仓 v2 的接入层（D2）

职责：
  · 从 wave_ladder 读 operation；
  · 从 yellow_line_daily 读 spread5；
  · 从 wolf_discipline 读总仓位目标（取不到 use trend_core 映射）；
  · 调 trend_core.decide() 得到 core_state；
  · 开关 WOLF_CORE_V2 默认 0；关=不产生任何新行为。

只做“读上下文 + 调纯函数”，不写生产表、不下单。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

DATA = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))
if not os.path.isdir(DATA):
    DATA = "data"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trend_core import CoreInputs, CoreDecision, decide, TOTAL_TARGET  # noqa: E402


def enabled() -> bool:
    return str(os.getenv("WOLF_CORE_V2", "0")).strip().lower() in ("1", "true", "yes", "on")


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    for p in (path, os.path.join("data", os.path.basename(path))):
        try:
            if os.path.exists(p):
                return json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
    return None


def read_operation(asof: Optional[str] = None) -> Optional[str]:
    """优先现算 wave_ladder；失败读 artifact。"""
    try:
        import wave_ladder as wl
        op = wl.ladder_operation(asof or None)
        if op:
            return str(op)
    except Exception:
        pass
    for name in ("wave_state.json", "wave_ladder.json"):
        obj = _read_json(os.path.join(DATA, name))
        if obj and obj.get("operation"):
            return str(obj["operation"])
    return None


def read_spread5(asof: Optional[str] = None) -> Optional[float]:
    obj = _read_json(os.path.join(DATA, "yellow_line_daily.json"))
    if not obj:
        return None
    rows = obj.get("rows") or []
    d = str(asof or "")[:8]
    for r in reversed(rows):
        rd = str(r.get("date") or "")
        if not d or rd <= d:
            try:
                return float(r.get("spread5"))
            except Exception:
                return None
    return None


def total_target(operation: Optional[str]) -> float:
    """总仓位档：v2 以 trend_core.TOTAL_TARGET 为准。

    刻意不 import backend.app.services.wolf_discipline：该包会拉起 scheduler/DB 初始化，
    在 stock_confirm_judge 这种短任务里会挂住。需要与生产配置对齐时，由调用方显式传
    total_override，或后续用轻量配置读取替代。
    """
    op = str(operation or "exit")
    return float(TOTAL_TARGET.get(op, 0.0))


def market_context(asof: Optional[str] = None) -> Dict[str, Any]:
    op = read_operation(asof)
    spread = read_spread5(asof)
    return {"asof": str(asof or "")[:8], "operation": op, "spread5": spread,
            "total_target_pct": total_target(op), "source": "wave_ladder+yellow_line"}


def decide_market(core_exists: bool = False, core_pct: float = 0.0,
                  mainline_confirmed: bool = True, leader_ok: bool = True,
                  break_flags: Tuple[str, ...] = (), asof: Optional[str] = None,
                  operation: Optional[str] = None, spread5: Optional[float] = None,
                  total_override: Optional[float] = None) -> Dict[str, Any]:
    """市场级 core 决策；operation/spread5 可显式传入（测试/回放）。"""
    op = operation if operation is not None else read_operation(asof)
    sp = spread5 if spread5 is not None else read_spread5(asof)
    if not op:
        op = "exit"
    d: CoreDecision = decide(CoreInputs(operation=op, mainline_confirmed=mainline_confirmed,
                                       leader_ok=leader_ok, spread5=sp,
                                       core_exists=core_exists, core_pct=core_pct,
                                       break_flags=tuple(break_flags or ())))
    total = d.total_target_pct if total_override is None else float(total_override)
    return {"asof": str(asof or "")[:8], "operation": op, "spread5": sp,
            "total_target_pct": total, "state": d.state, "action": d.action,
            "core_target_pct": d.core_target_pct, "t_target_pct": d.t_target_pct,
            "cash_pct": d.cash_pct, "reason": d.reason, "flags": list(d.flags)}


def write_core_state(asof: Optional[str] = None, **kw) -> Optional[str]:
    """写 data/core_state.json（影子/审计用；开关关时不写）。"""
    if not enabled():
        return None
    try:
        ctx = decide_market(asof=asof, **kw)
        os.makedirs(DATA, exist_ok=True)
        p = os.path.join(DATA, "core_state.json")
        json.dump(ctx, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return p
    except Exception:
        return None
