# -*- coding: utf-8 -*-
"""狼大风格选股 v0（P1 原型，未接入生产）。

抽象（见 docs/wolf-selection-feature-abstraction.md）：
  强中期主题（r20 前 3-5） × 主题内强票（r60/amt 高分位）
  × 入场模式（pullback 缩量回调 / breakout 放量突破） − 风险排除。

与旧 pick_v2 的区别：
  · 特征全部是**主题内分位**，不是全市场分位；
  · 先分 role/mode，再排序，不用单一线性 leader 分；
  · 默认不选 LOW/MID/靠近前低的票（旧闸门选反了人群）。
"""
from __future__ import annotations

import os
import sqlite3
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

DEFAULT_BARS = ".dsh-tmp/buyside/bars.parquet"
DEFAULT_POOL = ".dsh-tmp/buyside/prod_data/stock_pool.db"
MIN_AMT20 = 2e5          # bars.amount 单位≈千元；≈2 亿元
LIMIT_PCT = {"main": 9.7, "star": 19.7, "bj": 29.7}


def limit_threshold(code: str) -> float:
    c = str(code)
    if c.startswith(("300", "688")):
        return LIMIT_PCT["star"]
    if c.startswith(("8", "4", "92")):
        return LIMIT_PCT["bj"]
    return LIMIT_PCT["main"]
MIN_PEERS = 30


def _theme_kw() -> Dict[str, List[str]]:
    import sys
    if "backend" not in sys.path:
        sys.path.insert(0, "backend")
    from app.services.wolf_mainline_select import THEME_CONCEPT_KW  # noqa: E402
    return THEME_CONCEPT_KW


def load_theme_map(pool_db: str = DEFAULT_POOL) -> Tuple[Dict[str, set], Dict[str, Tuple[str, str]]]:
    con = sqlite3.connect(pool_db)
    meta = {c: (n, s) for c, n, s in con.execute("select ts_code,name,industry from stock_pool")}
    c2s: Dict[str, set] = {}
    for code, concept in con.execute("select ts_code,concept_name from stock_concept_map"):
        c2s.setdefault(concept, set()).add(code)
    out = {}
    for th, kws in _theme_kw().items():
        s = set()
        for c, stocks in c2s.items():
            if any(k in c for k in kws):
                s |= stocks
        out[th] = s
    return out, meta


def compute_features(bars_path: str = DEFAULT_BARS, as_of: Optional[str] = None) -> pd.DataFrame:
    df = pd.read_parquet(bars_path, columns=["ts_code", "trade_date", "close", "low", "high", "amount", "pct_chg", "is_st"])
    if as_of:
        df = df[df["trade_date"] <= str(as_of)]
    df["limit_up"] = df["pct_chg"].fillna(0) >= df["ts_code"].map(limit_threshold) - 0.3
    df = df.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = df.groupby("ts_code", group_keys=False)
    df["amt20"] = g["amount"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["amt5"] = g["amount"].transform(lambda s: s.rolling(5, min_periods=4).mean())
    df["low20"] = g["low"].transform(lambda s: s.rolling(20, min_periods=15).min())
    df["high20"] = g["high"].transform(lambda s: s.rolling(20, min_periods=15).max())
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["r20"] = g["close"].transform(lambda s: s / s.shift(20) - 1)
    df["r60"] = g["close"].transform(lambda s: s / s.shift(60) - 1)
    df["volr"] = df["amt5"] / df["amt20"]
    df["dist"] = df["close"] / df["low20"] - 1
    return df


def theme_strength(df: pd.DataFrame, theme_map: Dict[str, set], as_of: str) -> Dict[str, Dict[str, Any]]:
    piv = df.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    ret = piv.pct_change()
    r5, r20 = {}, {}
    for th, stocks in theme_map.items():
        cols = [c for c in stocks if c in ret.columns]
        if not cols:
            continue
        r5[th] = ret[cols].mean(axis=1).rolling(5).sum().get(as_of)
        r20[th] = ret[cols].mean(axis=1).rolling(20).sum().get(as_of)
    rank5 = {th: sum(1 for v in r5.values() if pd.notna(v) and v > r5[th]) + 1 for th in r5}
    rank20 = {th: sum(1 for v in r20.values() if pd.notna(v) and v > r20[th]) + 1 for th in r20}
    return {th: {"r5": r5.get(th), "r20": r20.get(th),
                 "r5_rank": rank5.get(th), "r20_rank": rank20.get(th),
                 "n_themes": len(r5)} for th in r5}


def classify_role(r60_pct: float, amt_pct: float, sub_role: str = "") -> str:
    if r60_pct >= 0.70 and amt_pct >= 0.80:
        return "leader"
    if sub_role in ("材料", "设备", "上游") and r60_pct >= 0.60 and amt_pct >= 0.70:
        return "upstream"
    if r60_pct >= 0.55 and amt_pct >= 0.55:
        return "second"
    return "reject"


def classify_mode(volr: float, dist: float, close: float, ma20: float, high20: float,
                  amt_pct: float = 1.0, volr_pct: float = 1.0) -> str:
    # 他的“缩量/放量”是**主题内相对**，不是绝对量比：
    #   英维克 2026-01-19 绝对 volr=0.92（不低），但主题内 volr 分位=0.10 → 仍是缩量回调。
    if (pd.notna(volr_pct) and volr_pct <= 0.50 and 0.02 <= dist <= 0.35
            and close >= ma20 * 0.97 and amt_pct >= 0.50):
        return "pullback"
    if (pd.notna(volr_pct) and volr_pct >= 0.85 and 0.30 <= dist <= 0.80
            and close >= high20 * 0.995 and amt_pct >= 0.60):
        return "breakout"
    return "watch"


def _sub_role(concepts: Sequence[str]) -> str:
    s = " ".join(concepts)
    if any(k in s for k in ("材料", "光刻", "封测", "存储")):
        return "材料"
    if any(k in s for k in ("设备", "机床")):
        return "设备"
    if any(k in s for k in ("上游", "光器件", "光通信")):
        return "上游"
    return ""


def select_for_theme(df: pd.DataFrame, theme: str, strength: Dict[str, Any],
                     stocks: set, meta: Dict[str, Tuple[str, str]],
                     concept_map: Optional[Dict[str, List[str]]] = None,
                     topn: int = 5) -> List[Dict[str, Any]]:
    last_day = df["trade_date"].max()
    peers = df[(df["trade_date"] == last_day) & (df["ts_code"].isin(stocks))].copy()
    peers = peers[(~peers["is_st"].fillna(False)) & (peers["amt20"] > MIN_AMT20)
                  & (~peers.get("limit_up", False).fillna(False))]
    peers = peers.dropna(subset=["r60", "amt20", "volr", "dist", "ma20", "high20"])
    if len(peers) < MIN_PEERS:
        return []
    peers["r60_pct"] = peers["r60"].rank(pct=True)
    peers["amt_pct"] = peers["amt20"].rank(pct=True)
    peers["volr_pct"] = peers["volr"].rank(pct=True)
    theme_score = 0.0
    r20r = int(strength.get("r20_rank") or 99)
    r5r = int(strength.get("r5_rank") or 99)
    if r20r <= 1:
        theme_score = 1.0
    elif r20r <= 3:
        theme_score = 0.85
    elif r20r <= 5:
        theme_score = 0.55
    if 6 <= r5r <= 13:
        theme_score = min(1.0, theme_score + 0.10)   # 短期回调加分
    elif r5r <= 3:
        theme_score = min(1.0, theme_score + 0.05)   # 短期加速只适合 breakout

    out = []
    for _, r in peers.iterrows():
        sub = _sub_role((concept_map or {}).get(r.ts_code, []))
        role = classify_role(float(r.r60_pct), float(r.amt_pct), sub)
        if role == "reject":
            continue
        mode = classify_mode(float(r.volr), float(r.dist), float(r.close), float(r.ma20),
                             float(r.high20), float(r.amt_pct), float(r.volr_pct))
        if mode == "watch":
            continue
        # v0.2：按前向检验（jobs/eval_pick_features.py）只保留有增量的组合：
        #   leader/breakout +1.50%、second/breakout +0.76%、upstream/pullback +1.00%（5 日）
        #   leader/pullback +0.07%、second/pullback +0.07%、second/watch -0.28% → 不做
        # v0.2.1：只保留前向检验最干净的两组：leader/breakout、second/breakout。
        # upstream/pullback 虽 +1.0%，但“上游”标签太噪（跨主题概念串味），先放研究桶，不进主选。
        # v0.4：按重读结果，主模式改为 leader/pullback（左侧/缩量回调），不再做 breakout。
        allowed = (role == "leader" and mode == "pullback")
        if not allowed:
            continue
        if float(r.close) >= float(r.high20) * 0.995:      # 不在 20 日新高
            continue
        if bool(r.get("limit_up", False)):                  # 不买涨停
            continue
        if float(r.amt_pct) < 0.80:
            continue
        role_score = {"leader": 1.0, "upstream": 0.85, "second": 0.75}.get(role, 0.0)
        entry_score = 1.0 if mode == "breakout" else 0.9
        score = 0.40 * role_score + 0.35 * theme_score + 0.25 * entry_score
        name, industry = meta.get(r.ts_code, ("?", "?"))
        out.append({
            "code": r.ts_code, "name": name, "industry": industry,
            "theme": theme, "role": role, "entry_mode": mode,
            "score": round(float(score), 4),
            "factors": {"r60_pct": round(float(r.r60_pct), 3), "amt20_pct": round(float(r.amt_pct), 3),
                        "volr": round(float(r.volr), 3), "volr_pct": round(float(r.volr_pct), 3),
                        "dist_low20": round(float(r.dist), 3),
                        "r60": round(float(r.r60) * 100, 1), "theme_r20_rank": r20r,
                        "theme_r5_rank": r5r},
            "invalidation": "跌破 MA20/前低" if mode == "pullback" else "跌回突破位",
            "stop_price": (round(min(float(r.close) * 0.97, float(r.ma20) * 0.98), 2)
                           if mode == "pullback" else round(float(r.ma20) * 0.99, 2)),
            "hold_days": 10,
            "entry": "signal_close",
            "position_weight": "equal",
            "risk_note": "单票≤10%，跌破 MA20×0.99 止损" if mode == "breakout" else "单票≤10%，跌破止损即走",
        })
    # v0.3：score 五分位不单调，不用 score 排序；leader 优先，同角色内量比越低越优先。
    out.sort(key=lambda x: (0 if x["role"] == "leader" else 1, x["factors"]["volr"],
                            -x["factors"]["amt20_pct"], x["code"]))
    return out[:topn]


def index_regime(as_of: str, index_path: str = "data/index_daily_000001_SH.json") -> Dict[str, Any]:
    """指数短期 regime（PIT）：r5 + 是否站上 MA20。"""
    try:
        import json
        obj = json.load(open(index_path, encoding="utf-8"))
        ix = pd.DataFrame(obj["rows"], columns=["trade_date", "close", "high", "amount"]).sort_values("trade_date")
        ix = ix[ix.trade_date <= str(as_of)]
        if len(ix) < 21:
            return {"r5": None, "above_ma20": None, "date": None}
        last = ix.iloc[-1]
        r5 = float(last.close / ix.iloc[-6].close - 1)
        ma20 = float(ix.close.tail(20).mean())
        ma60 = float(ix.close.tail(60).mean()) if len(ix) >= 60 else None
        ma120 = float(ix.close.tail(120).mean()) if len(ix) >= 120 else None
        return {"r5": round(r5, 4), "above_ma20": bool(last.close >= ma20),
                "above_ma60": bool(ma60 is not None and last.close >= ma60),
                "above_ma120": bool(ma120 is not None and last.close >= ma120),
                "close": round(float(last.close), 2), "ma20": round(ma20, 2),
                "ma60": round(ma60, 2) if ma60 else None, "ma120": round(ma120, 2) if ma120 else None,
                "date": str(last.trade_date)}
    except Exception:
        return {"r5": None, "above_ma20": None, "date": None}


def select_for_directions(directions: Sequence[str], as_of: str,
                          bars_path: str = DEFAULT_BARS, pool_db: str = DEFAULT_POOL,
                          topn: int = 10, require_regime: bool = True,
                          regime_mode: str = "ma120",
                          index_path: str = "data/index_daily_000001_SH.json") -> Dict[str, Any]:
    regime = index_regime(as_of, index_path)
    blocked = None
    if require_regime:
        if regime_mode == "ma120" and not regime.get("above_ma120"):
            blocked = "index_below_ma120"
        elif regime_mode == "ma60" and not regime.get("above_ma60"):
            blocked = "index_below_ma60"
        elif regime_mode == "r5" and (regime.get("r5") is None or regime["r5"] < 0):
            blocked = "index_r5<0"
    if blocked:
        return {"as_of": as_of, "regime": regime, "blocked": blocked,
                "themes": {}, "candidates": []}
    theme_map, meta = load_theme_map(pool_db)
    df = compute_features(bars_path, as_of)
    if df.empty:
        return {"as_of": as_of, "regime": regime, "themes": {}, "candidates": []}
    strength = theme_strength(df, theme_map, str(as_of))
    con = sqlite3.connect(pool_db)
    concept_map: Dict[str, List[str]] = {}
    for code, concept in con.execute("select ts_code,concept_name from stock_concept_map"):
        concept_map.setdefault(code, []).append(concept)
    themes, candidates = {}, []
    for th in directions:
        if th not in theme_map:
            continue
        if int(strength.get(th, {}).get("r20_rank") or 99) > 5:
            themes[th] = {"strength": strength.get(th), "skipped": "theme_r20_rank>5"}
            continue
        rows = select_for_theme(df, th, strength.get(th, {}), theme_map[th], meta, concept_map, topn)
        themes[th] = {"strength": strength.get(th), "n": len(rows)}
        candidates.extend(rows)
    candidates.sort(key=lambda x: (-x["score"], x["code"]))
    return {"as_of": as_of, "regime": regime, "themes": themes, "candidates": candidates}
