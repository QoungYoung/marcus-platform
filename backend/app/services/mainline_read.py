# -*- coding: utf-8 -*-
"""mainline_read.py — 主线/赛道/主题的只读读取层。

只做三件事：
  1. 读 data/mainline/{as_of}/theme.json / track.json / mainline.json；
  2. 把 symbol 或 theme_id/track_id 映射到 theme/track；
  3. 返回 thesis_valid / review_required / caps，供现有仓位函数读取。

不计算仓位、不改文件、不取实时行情。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

import yaml


def _workspace() -> str:
    try:
        from app.config import get_settings
        return str(get_settings().workspace_path)
    except Exception:
        return os.environ.get("MARCUS_WORKSPACE", os.getcwd())


def _mainline_root() -> str:
    return os.path.join(_workspace(), "data", "mainline")


def latest_asof() -> Optional[str]:
    root = _mainline_root()
    if not os.path.isdir(root):
        return None
    dates = sorted(d for d in os.listdir(root)
                   if d.isdigit() and len(d) == 8
                   and os.path.exists(os.path.join(root, d, "theme.json")))
    return dates[-1] if dates else None


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _load_yaml(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _theme_index() -> Dict[str, list]:
    """leader/ladder code -> [theme id]；同一股票可属于多个主题。"""
    cfg = _load_yaml(os.path.join(_workspace(), "config", "mainline", "generated", "universe_theme.yaml"))
    out: Dict[str, list] = {}
    for t in (cfg.get("themes") or []) + (cfg.get("satellite_themes") or []):
        tid = str(t.get("id"))
        for x in [t.get("leader")] + (t.get("ladder") or []):
            if x and x.get("code"):
                out.setdefault(str(x["code"]), []).append({"theme_id": tid, "name": t.get("name"),
                                                          "role": "leader" if x is t.get("leader") else "ladder"})
    return out


def _track_index() -> Dict[str, Dict[str, Any]]:
    cfg = _load_yaml(os.path.join(_workspace(), "config", "mainline", "generated", "universe_track.yaml"))
    out: Dict[str, Dict[str, Any]] = {}
    for t in (cfg.get("tracks") or []) + (cfg.get("satellite_tracks") or []):
        tid = str(t.get("id"))
        for x in [t.get("leader")] + (t.get("ladder") or []):
            if x and x.get("code"):
                out[str(x["code"])] = {"track_id": tid, "name": t.get("name")}
    return out


def _find_theme(rows: list, theme_id: Optional[str], symbol: Optional[str]) -> Optional[Dict[str, Any]]:
    if theme_id:
        for r in rows:
            if str(r.get("id")) == str(theme_id):
                return r
    if symbol:
        hits = _theme_index().get(str(symbol)) or []
        ids = {h["theme_id"] for h in hits}
        cand = [r for r in rows if str(r.get("id")) in ids]
        if cand:
            # 同一股票属于多个主题时：优先非 avoid，再按 rank/score
            cand.sort(key=lambda r: (r.get("verdict") == "avoid", r.get("rank", 99)))
            return cand[0]
    return None


def _find_track(rows: list, track_id: Optional[str], symbol: Optional[str]) -> Optional[Dict[str, Any]]:
    if track_id:
        for r in rows:
            if str(r.get("id")) == str(track_id):
                return r
    if symbol:
        hit = _track_index().get(str(symbol))
        if hit:
            for r in rows:
                if str(r.get("id")) == hit["track_id"]:
                    return r
    return None


def load_position_context(symbol: Optional[str] = None, theme_id: Optional[str] = None,
                          track_id: Optional[str] = None, asof: Optional[str] = None) -> Dict[str, Any]:
    """返回 {available, as_of, theme_id, track_id, thesis_valid, review_required, failed_criteria, caps, verdict}。"""
    date = asof or latest_asof()
    if not date:
        return {"available": False, "reason": "no mainline data"}
    root = os.path.join(_mainline_root(), date)
    theme = _load_json(os.path.join(root, "theme.json"))
    track = _load_json(os.path.join(root, "track.json"))
    mainline = _load_json(os.path.join(root, "mainline.json"))
    rows = [r for r in (theme.get("themes") or []) if "error" not in r]
    trow = _find_theme(rows, theme_id, symbol)
    krow = _find_track([r for r in (track.get("tracks") or []) if "error" not in r], track_id, symbol)
    if not trow and not krow:
        return {"available": False, "reason": "symbol/theme not in mainline universe", "as_of": date}
    if not krow and trow and trow.get("parent_track"):
        krow = next((r for r in (track.get("tracks") or []) if str(r.get("id")) == str(trow.get("parent_track"))), None)
    src = trow or krow
    return {
        "available": True,
        "as_of": date,
        "mainline_confirmed": bool(mainline.get("confirmed")),
        "mainline_exists": bool(mainline.get("mainline_exists", bool(mainline.get("mainline")))),
        "mainline_active": bool(mainline.get("mainline_active", False)),
        "tradeable": bool(mainline.get("tradeable", mainline.get("confirmed"))),
        "status": mainline.get("status"),
        "regime": mainline.get("regime"),
        "mainline": {
            "mainline": mainline.get("mainline"),
            "name": mainline.get("name"),
            "sub_mainline": mainline.get("sub_mainline"),
            "confirmed": bool(mainline.get("confirmed")),
            "mainline_exists": bool(mainline.get("mainline_exists", bool(mainline.get("mainline")))),
            "mainline_active": bool(mainline.get("mainline_active", False)),
            "tradeable": bool(mainline.get("tradeable", mainline.get("confirmed"))),
            "status": mainline.get("status"),
            "regime": mainline.get("regime"),
        },
        "theme_id": trow.get("id") if trow else None,
        "theme_name": trow.get("name") if trow else None,
        "track_id": krow.get("id") if krow else trow.get("parent_track") if trow else None,
        "track_name": krow.get("name") if krow else None,
        "verdict": src.get("verdict"),
        "thesis_valid": bool(src.get("thesis_valid")) if "thesis_valid" in src else None,
        "review_required": bool(src.get("review_required")) if "review_required" in src else None,
        "failed_criteria": src.get("failed_criteria") or [],
        "criteria_pass": src.get("criteria_pass"),
        "caps": src.get("caps") or {},
        "holding_days": src.get("holding_days") or theme.get("holding_days"),
    }
