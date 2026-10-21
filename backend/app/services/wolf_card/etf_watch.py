# -*- coding: utf-8 -*-
"""同主题 ETF 排序：流动性 + 相对强度 + 溢价 + 角色。

数据：data/etf/etf_daily.parquet、etf_nav.parquet（fund_daily/fund_nav）。
原则：只做可观测排序，不写死“他买哪只”；159516/588170/159316 只作为工具 override。
"""
from __future__ import annotations
import json, os
from typing import Any, Dict, List
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))
DATA = os.path.join(ROOT, "data")
_CACHE = {}

MANUAL = {
    "159516.SZ": {"name": "半导体设备ETF", "theme": "半导体/芯片", "role": "manual", "sleeve": "T"},
    "588170.SH": {"name": "科创半导体ETF", "theme": "半导体/芯片", "role": "manual", "sleeve": "T"},
    "159316.SZ": {"name": "港股创新药ETF", "theme": "医药", "role": "manual", "sleeve": "low_position"},
}


def _load(name):
    if name not in _CACHE:
        try:
            _CACHE[name] = pd.read_parquet(os.path.join(DATA, "etf", name + ".parquet"))
        except Exception:
            _CACHE[name] = pd.DataFrame()
    return _CACHE[name]


def _map() -> Dict[str, List[dict]]:
    out = {}
    try:
        d = json.load(open(os.path.join(DATA, "etf_theme_map_pi.json"), encoding="utf-8"))
        for t in d.get("themes", []):
            rows = out.setdefault(t["theme"], [])
            for key, role in (("primary", "primary"), ("optional", "optional")):
                for x in t.get(key) or []:
                    rows.append({"code": x.get("ts_code"), "name": x.get("name"), "theme": t["theme"],
                                 "role": role, "sleeve": "core" if role == "primary" else "T"})
    except Exception:
        pass
    for code, x in MANUAL.items():
        out.setdefault(x["theme"], []).append({"code": code, "name": x["name"], "theme": x["theme"],
                                               "role": x["role"], "sleeve": x["sleeve"]})
    return out


def _pct(vals, x):
    vals = [v for v in vals if v is not None]
    if not vals or x is None:
        return 0.5
    return sum(1 for v in vals if v < x) / len(vals)


def rank_etfs(theme: str, date8: str, top: int = 5, min_amt20: float = 5000.0) -> List[dict]:
    daily, nav = _load("etf_daily"), _load("etf_nav")
    share, basic = _load("etf_share"), _load("etf_basic")
    bkey = {}
    if not basic.empty:
        for _, b in basic.iterrows():
            bm = str(b.get("benchmark") or "")
            bm = bm.replace("收益率", "").replace("×100%", "").replace("%", "").strip()
            bkey[str(b.get("ts_code"))] = bm
    rows = _map().get(theme, [])
    if daily.empty:
        return rows[:top]
    cand = []
    for x in rows:
        c = x["code"]
        d = daily[(daily.ts_code == c) & (daily.trade_date <= date8)].sort_values("trade_date").tail(30)
        if len(d) == 0:
            continue
        close = d.close.astype(float).values
        amt = d.amount.astype(float).values
        # 新上市 ETF 历史不足 20 日也保留，标 new_listing；不能因为没 20 日就丢掉
        new_listing = len(d) < 10
        r5 = close[-1] / close[-6] - 1 if len(close) >= 6 else None
        r20 = close[-1] / close[-21] - 1 if len(close) >= 21 else None
        amt20 = float(amt[-20:].mean()) if len(amt) >= 20 else float(amt.mean())
        if amt20 < min_amt20:
            continue
        amt5 = float(amt[-5:].mean()) if len(amt) >= 5 else float(amt.mean())
        premium = None
        if not nav.empty:
            n = nav[(nav.ts_code == c) & (nav.nav_date <= date8)].sort_values("nav_date").tail(1)
            if len(n) and float(n.iloc[0].unit_nav or 0) > 0:
                premium = float(close[-1]) / float(n.iloc[0].unit_nav) - 1
        scale = None
        if not share.empty:
            sh = share[(share.ts_code == c) & (share.trade_date <= date8)].sort_values("trade_date").tail(1)
            if len(sh):
                scale = float(sh.iloc[0].fd_share) * float(close[-1])
        cand.append({**x, "close": round(float(close[-1]), 3), "r5": r5, "r20": r20,
                     "new_listing": new_listing,
                     "amt20": amt20, "amt_chg": round(amt5 / amt20, 3) if amt20 else None,
                     "premium": round(premium, 4) if premium is not None else None,
                     "scale": scale, "benchmark": bkey.get(c)})
    if not cand:
        return rows[:top]
    liqs = [x["amt20"] for x in cand]
    r20s = [x["r20"] for x in cand]
    r5s = [x["r5"] for x in cand]
    scales = [x["scale"] for x in cand if x.get("scale")]
    for x in cand:
        liq_pct = _pct(liqs, x["amt20"])
        r20_pct = _pct(r20s, x["r20"])
        r5_pct = _pct(r5s, x["r5"])
        scale_pct = _pct(scales, x.get("scale")) if x.get("scale") else 0.5
        prem = x.get("premium")
        prem_score = (0.5 if prem is None else
                      1.0 if abs(prem) <= 0.01 else (0.5 if abs(prem) <= 0.02 else 0.0))
        role_score = 1.0 if x["role"] == "primary" else (0.9 if x["role"] == "manual" else 0.6)
        x["score"] = round(0.35 * liq_pct + 0.20 * r20_pct + 0.15 * r5_pct
                           + 0.10 * role_score + 0.10 * prem_score + 0.10 * scale_pct, 4)
        x["reason"] = "liq=%.2f r20=%.2f r5=%.2f scale=%.2f role=%s prem=%s amt20=%.0f" % (
            liq_pct, r20_pct, r5_pct, scale_pct, x["role"], x.get("premium"), x["amt20"])
    cand.sort(key=lambda x: -x["score"])
    # 同 benchmark 去重：同指数/同跟踪标的只留分数最高的一只
    seen_b, uniq = set(), []
    for x in cand:
        bm = x.get("benchmark")
        if bm and bm in seen_b:
            continue
        if bm:
            seen_b.add(bm)
        uniq.append(x)
    return uniq[:top]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--theme", required=True)
    ap.add_argument("--date", required=True)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--min-amt20", type=float, default=5000.0)
    a = ap.parse_args()
    import json as _json
    print(_json.dumps(rank_etfs(a.theme, a.date, a.top, a.min_amt20), ensure_ascii=False, indent=1))
