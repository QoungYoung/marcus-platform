# -*- coding: utf-8 -*-
"""方向闸：r20 + breadth 只做“新底仓资格”，不改 share5 横截面排序。

- active：r20>0 且 breadth20>=阈值 → 允许新底仓；
- t_only：r20<=0 但 r5>0 → 只允许 T/watch；
- blocked：r20<=0 且 r5<=0 → 不做；
- unknown：无数据。
"""
from __future__ import annotations
import os
from typing import Any, Dict
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")
_CACHE = {}


def _load(name):
    if name not in _CACHE:
        try:
            _CACHE[name] = pd.read_parquet(os.path.join(DATA, name))
        except Exception:
            _CACHE[name] = pd.DataFrame()
    return _CACHE[name]


def theme_gate(theme: str, date8: str, min_r20: float = 0.0, min_breadth: float = 0.4) -> Dict[str, Any]:
    s, b = _load("theme_strength_daily.parquet"), _load("theme_breadth_daily.parquet")
    out = {"theme": theme, "date": date8, "r20": None, "r5": None, "share5": None,
           "breadth20": None, "breadth_chg5": None, "status": "unknown", "new_base": False}
    if not s.empty:
        x = s[(s.theme == theme) & (s.trade_date <= date8)].sort_values("trade_date").tail(1)
        if len(x):
            r = x.iloc[0]
            out.update({"r20": round(float(r.r20), 4), "r5": round(float(r.r5), 4),
                        "share5": round(float(r.share5), 4)})
    if not b.empty:
        x = b[(b.theme == theme) & (b.trade_date <= date8)].sort_values("trade_date").tail(1)
        if len(x):
            r = x.iloc[0]
            out.update({"breadth20": round(float(r.breadth20), 4),
                        "breadth_chg5": round(float(r.breadth_chg5), 4) if pd.notna(r.breadth_chg5) else None})
    if out["r20"] is None:
        return out
    new_base = out["r20"] > min_r20 and (out["breadth20"] is not None and out["breadth20"] >= min_breadth)
    if new_base:
        out["status"] = "active"; out["new_base"] = True
    elif out["r20"] > min_r20:
        out["status"] = "watch"          # 趋势在，但广度不够
    elif (out["r5"] or 0) > 0:
        out["status"] = "t_only"         # 反弹/T，不做新底仓
    else:
        out["status"] = "blocked"
    return out


def attach(directions: list, date8: str, min_r20: float = 0.0, min_breadth: float = 0.4) -> list:
    return [{**d, "gate": theme_gate(d["theme"], date8, min_r20, min_breadth)} for d in directions]
