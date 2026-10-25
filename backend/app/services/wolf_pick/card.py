# -*- coding: utf-8 -*-
"""决策卡构建（P0-6）。

输出一张可被 CLI 直接落盘的 JSON：
  meta / regime / directions / candidates / key_levels / plan / his_side（仅 oracle）。
P0 只接入：主线状态、波浪状态、ETF 兜底、关键位观察；股票池在 P1 接入。
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import __version__
from .direction import select_directions
from .key_levels import DEFAULT_PATH as KEY_LEVELS_DEFAULT
from .key_levels import evaluate as evaluate_levels
from .key_levels import load_levels
from .state_guard import check_files, load_json

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
_APPS_MAIN = os.path.join(_REPO, "apps", "main_line")
if _APPS_MAIN not in sys.path:
    sys.path.insert(0, _APPS_MAIN)
try:
    from wave_ladder import enrich_wave_state  # type: ignore
except Exception:  # pragma: no cover
    def enrich_wave_state(ws):  # type: ignore
        return ws


# ⚠️ 本模块当前仅测试引用、未接生产；仓位数字对齐 defense_state（2026-10-25：t_only 50 / exit 15）。
_OP_PLAN = {
    "build": {"new_entry_allowed": True, "max_total_position": 75, "t_only": False},
    "t_only": {"new_entry_allowed": False, "max_total_position": 50, "t_only": True},
    "side": {"new_entry_allowed": True, "max_total_position": 50, "t_only": False},
    "defense": {"new_entry_allowed": False, "max_total_position": 30, "t_only": True},
    "exit": {"new_entry_allowed": False, "max_total_position": 15, "t_only": False},
}


def _read_labels(path: Optional[str], as_of8: str, lookback_days: int = 5) -> List[Dict[str, Any]]:
    """读当日标签；当日没有则回退到最近一个 <= as_of 的标签日（PIT 安全）。"""
    if not path or not os.path.exists(path):
        return []
    all_rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                d = str(obj.get("date") or "")[:8]
                if len(d) == 8:
                    all_rows.append((d, obj))
    except Exception:
        return []
    exact = [o for d, o in all_rows if d == as_of8]
    if exact:
        return exact
    import datetime as _dt
    try:
        ref = _dt.datetime.strptime(as_of8, "%Y%m%d").date()
    except ValueError:
        return []
    prior = []
    for d, o in all_rows:
        try:
            dd = _dt.datetime.strptime(d, "%Y%m%d").date()
        except ValueError:
            continue
        if 0 <= (ref - dd).days <= lookback_days:
            prior.append((dd, o))
    if not prior:
        return []
    latest = max(d for d, _ in prior)
    return [o for d, o in prior if d == latest]


def _his_side(labels: List[Dict[str, Any]]) -> Dict[str, Any]:
    doing, disclosed, not_doing, quotes = [], [], [], []
    doing_detail, not_doing_detail = [], []
    for x in labels:
        theme = x.get("theme")
        action = str(x.get("action") or "")
        inst = x.get("instrument") or {}
        if theme and action == "doing" and theme not in doing:
            doing.append(theme)
            doing_detail.append({"theme": theme, "sub_theme": x.get("sub_theme")})
        if theme and action == "not_doing" and theme not in not_doing:
            not_doing.append(theme)
            not_doing_detail.append({"theme": theme, "sub_theme": x.get("sub_theme")})
        code = inst.get("code")
        name = inst.get("name")
        if code and code not in disclosed:
            disclosed.append(code)
        elif name and name not in disclosed:
            disclosed.append(name)
        if x.get("quote"):
            quotes.append(x["quote"])
    return {"doing": doing, "disclosed": disclosed, "not_doing": not_doing,
            "doing_detail": doing_detail, "not_doing_detail": not_doing_detail,
            "quotes": quotes[:5], "n_labels": len(labels),
            "label_date": labels[0].get("date") if labels else None}


def _etf_fallback(data_dir: str, theme: str) -> Optional[Dict[str, Any]]:
    obj = load_json(os.path.join(data_dir, "etf_theme_map_pi.json"))
    for t in (obj.get("themes") or []):
        if str(t.get("theme") or "") != theme:
            continue
        primary = t.get("primary") or []
        if not primary:
            continue
        e = primary[0]
        ts = str(e.get("ts_code") or "")
        return {
            "code": ts.split(".")[0] if ts else None,
            "ts_code": ts,
            "name": e.get("name"),
            "kind": "etf",
            "theme": theme,
            "score": 0.5,
            "factors": {"direction": 0.5, "identifiability": 0.0, "cold_penalty": 0.0},
            "buy_zone": None,
            "trigger": None,
            "invalidation": None,
            "position_hint": "ETF 兜底（P0 股票池未接入）",
            "reason": e.get("reason") or "主题 primary ETF",
        }
    return None


def _plan(wave: Dict[str, Any], stale: Dict[str, Any]) -> Dict[str, Any]:
    op = str(wave.get("operation") or "t_only").lower()
    plan = dict(_OP_PLAN.get(op, _OP_PLAN["t_only"]))
    plan["operation"] = op
    plan["mode"] = wave.get("mode") or ""
    notes = []
    if plan["mode"] == "自救":
        notes.append("下跌1浪：禁新建仓；存量只借反弹/放量减仓或做T")
    if stale.get("wave", {}).get("stale"):
        plan.update({"new_entry_allowed": False, "max_total_position": min(plan["max_total_position"], 30),
                     "t_only": True})
        notes.append("wave_state 过期 → 降级为只做T/不开新底仓")
    if stale.get("mainline", {}).get("stale"):
        plan["max_total_position"] = min(plan["max_total_position"], 50)
        notes.append("main_line_state 过期 → 总仓位上限降至 50%% 以内（当前 %d%%）" % plan["max_total_position"])
    plan["notes"] = notes
    return plan


def build_card(data_dir: str, as_of: str, mode: str = "blind", topk: int = 3,
               labels_path: Optional[str] = None, key_levels_path: Optional[str] = None,
               key_levels_enabled: bool = False, with_stocks: bool = False,
               stock_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """构建单日决策卡。`mode=oracle` 时才读标签；`blind` 不读。"""
    as_of8 = "".join(ch for ch in str(as_of) if ch.isdigit())[:8]
    if len(as_of8) != 8:
        raise ValueError("as_of must be YYYYMMDD or YYYY-MM-DD: %r" % (as_of,))
    ms = load_json(os.path.join(data_dir, "main_line_state.json"))
    from app.services import wave_store as _ws   # ★ 步 3：PG 优先 ✓
    ws = _ws.get_wave() or load_json(os.path.join(data_dir, "wave_state.json"))
    stale = check_files(data_dir, as_of8)
    ws = enrich_wave_state(ws) or {}
    directions = select_directions(ms, topk=topk)

    feats = ws.get("features") or {}
    regime = {
        "wave": {k: ws.get(k) for k in
                 ("date", "level", "sub_level", "operation", "family", "stage", "status",
                  "mode", "confidence", "label_note", "reasons")},
        "index": {"close": feats.get("close"), "ma20": feats.get("ma20"),
                  "ma60": feats.get("ma60"), "break_low60": feats.get("break_low60"),
                  "break_low20": feats.get("break_low20")},
        "volume": {"amount": feats.get("amount"), "amount_yi": round((feats.get("amount") or 0) / 1e8, 1)
                   if feats.get("amount") else None},
    }

    candidates = []
    for d in directions:
        e = _etf_fallback(data_dir, d["theme"])
        if e:
            e["direction_rank"] = d["rank"]
            candidates.append(e)

    levels = load_levels(key_levels_path or KEY_LEVELS_DEFAULT)
    key_obs = evaluate_levels(levels, feats.get("close"), ws, amount_ratio=1.0, days_below=0,
                              enabled=bool(key_levels_enabled))

    card = {
        "meta": {
            "as_of": as_of8,
            "cutoff": "14:55",
            "mode": mode,
            "engine_version": __version__,
            "stale": stale,
            "disclaimer": "研究用途，不构成投资建议",
        },
        "regime": regime,
        "directions": directions,
        "candidates": candidates,
        "key_levels": {"enabled": bool(key_levels_enabled),
                       "conditional": any(x.get("conditional") for x in key_obs),
                       "levels": key_obs},
        "plan": _plan(ws, stale),
    }
    if mode == "oracle":
        card["his_side"] = _his_side(_read_labels(labels_path, as_of8))
    else:
        card["his_side"] = None
    if with_stocks:
        cfg = stock_config or {}
        try:
            from .stock_select import select_for_directions
            res = select_for_directions(
                [d["theme"] for d in directions], as_of8, topn=int(cfg.get("max_day", 5)),
                require_regime=True, regime_mode=str(cfg.get("regime_mode", "ma120")))
            cands = res.get("candidates") or []
            for c in cands:
                c["shadow"] = bool(cfg.get("shadow", True))
                c["hold_days"] = int(cfg.get("hold_days", 10))
                c["position_weight"] = "equal"
            card["stock_candidates"] = cands
            card["stock_selection"] = {"shadow": bool(cfg.get("shadow", True)),
                                       "regime": res.get("regime"),
                                       "blocked": res.get("blocked"),
                                       "config": cfg}
        except Exception as e:
            card["stock_candidates"] = []
            card["stock_selection"] = {"shadow": True, "error": "%s: %s" % (type(e).__name__, str(e)[:120])}
    return card
