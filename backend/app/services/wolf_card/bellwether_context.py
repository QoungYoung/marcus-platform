# -*- coding: utf-8 -*-
"""风向标的正确用途：环境/补涨/高切低闸，不是选股 alpha。

语料：
  · 2026-01-12「风向标没有死，自己的票没涨也拿着等补涨」
  · 2026-01-12「风向标死了就不能做了……千万不要高切低」
  · 2021-05-07「风向标的存在就是拿来看的，不是拿来做的」
  · 2025-12-06「风向标被摁跌停或更换了，换得快、接得上，只影响短期」

输出的是“允许做什么”，不是“买什么”。
"""
from __future__ import annotations
import json, os
from typing import Any, Dict, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT_CANDS = [
    os.path.join(_HERE, "..", "..", "..", ".."),
    os.path.join(_HERE, "..", "..", ".."),
    "/app",
]
ROOT = next((os.path.normpath(p) for p in _ROOT_CANDS
             if os.path.exists(os.path.normpath(os.path.join(p, "data", "core_p1", "bellwether_state_latest.json")))),
            os.path.normpath(_ROOT_CANDS[0]))
STATE_FILE = os.path.join(ROOT, "data/core_p1/bellwether_state_latest.json")

PERMISSIONS = {
    "alive": {
        "second_line_new": True,
        "high_cut_low": True,
        "hold_second_line": True,
        "reduce_theme": False,
        "risk": "low",
    },
    "rotated": {
        "second_line_new": True,
        "high_cut_low": True,
        "hold_second_line": True,
        "reduce_theme": False,
        "risk": "low",
    },
    "warning": {
        "second_line_new": False,
        "high_cut_low": False,
        "hold_second_line": True,
        "reduce_theme": False,
        "risk": "medium",
    },
    "dead": {
        "second_line_new": False,
        "high_cut_low": False,
        "hold_second_line": False,
        "reduce_theme": True,
        "risk": "high",
    },
    "stale": {
        "second_line_new": False,
        "high_cut_low": False,
        "hold_second_line": True,
        "reduce_theme": False,
        "risk": "unknown",
    },
    "unknown": {
        "second_line_new": False,
        "high_cut_low": False,
        "hold_second_line": True,
        "reduce_theme": False,
        "risk": "unknown",
    },
}


def permissions_for_state(state: str, successor: Optional[str] = None) -> Dict[str, Any]:
    p = dict(PERMISSIONS.get(str(state), PERMISSIONS["unknown"]))
    p["state"] = str(state)
    p["successor"] = successor
    p["purpose"] = "second_line/high_cut_low/hold_gate"
    return p


def load_latest(path: str = STATE_FILE) -> Dict[str, Any]:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {"date": None, "items": []}


def all_context(date: Optional[str] = None, path: str = STATE_FILE) -> Dict[str, Dict[str, Any]]:
    x = load_latest(path)
    if date and str(x.get("date")) != str(date):
        return {}
    out = {}
    for it in x.get("items", []):
        out[it.get("subtheme")] = {
            **it,
            "permissions": permissions_for_state(it.get("state"), it.get("successor_code")),
        }
    return out


def for_subtheme(subtheme: str, date: Optional[str] = None, path: str = STATE_FILE) -> Dict[str, Any]:
    ctx = all_context(date=date, path=path)
    return ctx.get(subtheme) or {
        "subtheme": subtheme,
        "state": "unknown",
        "permissions": permissions_for_state("unknown"),
    }
