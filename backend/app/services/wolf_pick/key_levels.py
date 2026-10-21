# -*- coding: utf-8 -*-
"""关键位观察（P0-3，默认只观察，不驱动交易）。

A/B 结论（docs/wolf-p0-ab-report.md）：上证 60 日新低 319 次，后 5/10/20 日平均
+0.68%/+0.97%/+1.38%，5 日收回率 60% ⇒ 机械“破位=离场”会卖在底部。
因此本模块只输出 `triggered/conditional` 观察位，**默认 enabled=False**，不改 plan。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

DEFAULT_PATH = "config/wave_key_levels.json"
_ALLOWED_STAGES = {"4-5", "down-1", "C杀"}


def load_levels(path: str = DEFAULT_PATH) -> List[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except Exception:
        return []
    if isinstance(obj, dict):
        obj = obj.get("levels") or []
    return [x for x in obj if isinstance(x, dict) and x.get("level") is not None]


def _conditional(wave: Optional[Dict[str, Any]]) -> bool:
    w = wave or {}
    fam = str(w.get("family") or "")
    stage = str(w.get("stage") or "")
    status = str(w.get("status") or "")
    return fam == "down" or (fam == "4" and stage in _ALLOWED_STAGES and status == "failed")


def evaluate(levels: List[Dict[str, Any]], close: Optional[float], wave: Optional[Dict[str, Any]],
             amount_ratio: float = 1.0, days_below: int = 0, enabled: bool = False) -> List[Dict[str, Any]]:
    """评估关键位。只有 enabled ∧ wave 条件 ∧ 收盘破 ∧ 放量 ∧ 2 日不收回 才算 triggered。"""
    out = []
    if close is None:
        return out
    cond = _conditional(wave)
    for lv in levels:
        try:
            level = float(lv.get("level"))
        except (TypeError, ValueError):
            continue
        broken = float(close) < level
        confirmed = broken and amount_ratio >= 1.2 and days_below >= 2
        out.append({
            "level": level,
            "kind": lv.get("kind") or "support",
            "source": lv.get("source"),
            "note": lv.get("note"),
            "distance_pct": round((float(close) - level) / level * 100.0, 3),
            "broken": broken,
            "conditional": cond,
            "confirmed": confirmed,
            "triggered": bool(enabled and cond and confirmed),
            "action": "down1" if (enabled and cond and confirmed) else "watch",
        })
    return out
