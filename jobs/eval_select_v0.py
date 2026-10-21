# -*- coding: utf-8 -*-
"""v0.2 选股器全样本前向评测。

规则（v0.2.1）：
  theme_r20_rank ≤5 ∧ theme_r5_rank ≤5
  role ∈ {leader, second}
  mode = breakout（close≥high20×0.995，0.30≤dist≤0.80）
  amt20_pct ≥0.70 ∧ volr ≤1.5
  每主题每日按 score 取 topN（默认 5）

口径：
  · 2026-01-01~2026-09-11；PIT：只用 D 日及以前；
  · 超额 = 个股 f5/f10 − 同主题同日等权；
  · 报均值/中位/胜率/日度 t/周块 t；按月、角色、主题拆分；
  · 随机基线：同主题同日随机（all peers / eligible peers）的期望超额。
输出：data/eval_select_v0.json
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, "backend")
sys.path.insert(0, "jobs")
from app.services.wolf_mainline_select import THEME_CONCEPT_KW  # noqa: E402

BARS = ".dsh-tmp/buyside/bars.parquet"
POOL = ".dsh-tmp/buyside/prod_data/stock_pool.db"
START, END = "20260101", "20260911"
TOPN = 5


def theme_map():
    con = sqlite3.connect(POOL)
    c2s = {}
    for code, concept in con.execute("select ts_code,concept_name from stock_concept_map"):
        c2s.setdefault(concept, set()).add(code)
    out = {}
    for th, kws in THEME_CONCEPT_KW.items():
        s = set()
        for c, stocks in c2s.items():
            if any(k in c for k in kws):
                s |= stocks
        out[th] = s
    return out


def sub_roles():
    con = sqlite3.connect(POOL)
    out = {}
    for code, concept in con.execute("select ts_code,concept_name from stock_concept_map"):
        s = out.get(code, "")
        if any(k in concept for k in ("材料", "光刻", "封测", "存储")):
            s = "材料"
        elif "设备" in concept and s != "材料":
            s = "设备"
        elif any(k in concept for k in ("上游", "光器件", "光通信")) and s not in ("材料", "设备"):
            s = "上游"
        out[code] = s
    return out


def build(start=START, end=END):
    df = pd.read_parquet(BARS, columns=["ts_code", "trade_date", "close", "low", "high", "amount", "pct_chg", "is_st"])
    df = df[(df.trade_date >= start) & (df.trade_date <= end)].sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    g = df.groupby("ts_code", group_keys=False)
    df["amt20"] = g["amount"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["amt5"] = g["amount"].transform(lambda s: s.rolling(5, min_periods=4).mean())
    df["low20"] = g["low"].transform(lambda s: s.rolling(20, min_periods=15).min())
    df["high20"] = g["high"].transform(lambda s: s.rolling(20, min_periods=15).max())
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["r60"] = g["close"].transform(lambda s: s / s.shift(60) - 1)
    df["volr"] = df["amt5"] / df["amt20"]
    df["dist"] = df["close"] / df["low20"] - 1
    df["f5"] = g["close"].transform(lambda s: s.shift(-5) / s - 1)
    df["f10"] = g["close"].transform(lambda s: s.shift(-10) / s - 1)
    df["f20"] = g["close"].transform(lambda s: s.shift(-20) / s - 1)
    df = df[(~df.is_st.fillna(False)) & (df.amt20 > 1e5) & df.f5.notna()]
    return df.dropna(subset=["r60", "volr", "dist", "ma20", "high20"])


def theme_ranks(df, tm):
    piv = df.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    ret = piv.pct_change()
    r5, r20 = {}, {}
    for th, stocks in tm.items():
        cols = [c for c in stocks if c in ret.columns]
        if not cols:
            continue
        r5[th] = ret[cols].mean(axis=1).rolling(5).sum()
        r20[th] = ret[cols].mean(axis=1).rolling(20).sum()
    r5 = pd.DataFrame(r5)
    r20 = pd.DataFrame(r20)
    return r5.rank(axis=1, ascending=False), r20.rank(axis=1, ascending=False), r5, r20


def theme_score(r20r, r5r):
    s = 1.0 if r20r <= 1 else 0.85 if r20r <= 3 else 0.65
    s += 0.10 if r5r <= 3 else 0.05
    return min(1.0, s)


def stat(series):
    s = pd.Series(series).dropna()
    n = len(s)
    if not n:
        return {}
    mean = float(s.mean())
    t = mean / (s.std() / np.sqrt(n)) if n > 1 and s.std() else None
    return {"n": int(n), "mean_pct": round(mean * 100, 3), "median_pct": round(float(s.median()) * 100, 3),
            "win": round(float((s > 0).mean()), 3),
            "t": round(float(t), 2) if t is not None else None}


def week_t(dates, vals):
    d = pd.DataFrame({"d": pd.to_datetime(dates), "v": vals}).dropna()
    if d.empty:
        return None
    d["w"] = d.d.dt.isocalendar().week.astype(int) + d.d.dt.year * 100
    wk = d.groupby("w").v.mean()
    return round(float(wk.mean() / (wk.std() / np.sqrt(len(wk)))), 2) if len(wk) > 1 and wk.std() else None


def main():
    df = build()
    # 指数 regime（PIT）：r5、是否站上 MA20
    idx = json.load(open("data/index_daily_000001_SH.json", encoding="utf-8"))
    ix = pd.DataFrame(idx["rows"], columns=["trade_date", "close", "high", "amount"]).sort_values("trade_date")
    ix = ix[(ix.trade_date >= START) & (ix.trade_date <= END)].reset_index(drop=True)
    ix["idx_r5"] = ix.close / ix.close.shift(5) - 1
    ix["idx_ma20"] = ix.close.rolling(20, min_periods=15).mean()
    ix["idx_above_ma20"] = ix.close >= ix.idx_ma20
    idx_map = ix.set_index("trade_date")[["idx_r5", "idx_above_ma20"]]
    tm = theme_map()
    sr = sub_roles()
    df["sub_role"] = df.ts_code.map(sr).fillna("")
    r5r, r20r, r5v, r20v = theme_ranks(df, tm)
    picks, all_elig = [], []
    for th, stocks in tm.items():
        sub = df[df.ts_code.isin(stocks)].copy()
        if len(sub) < 1000:
            continue
        for c in ("r60", "amt20", "volr"):
            sub[c + "_pct"] = sub.groupby("trade_date")[c].rank(pct=True)
        sub["role"] = np.select(
            [(sub.r60_pct >= 0.70) & (sub.amt20_pct >= 0.80),
             (sub.sub_role.isin(["材料", "设备", "上游"])) & (sub.r60_pct >= 0.60) & (sub.amt20_pct >= 0.70),
             (sub.r60_pct >= 0.55) & (sub.amt20_pct >= 0.55)],
            ["leader", "upstream", "second"], default="reject")
        sub["mode"] = np.select(
            [(sub.volr_pct <= 0.30) & (sub.dist >= 0.05) & (sub.dist <= 0.30) & (sub.close >= sub.ma20 * 0.97),
             (sub.volr_pct >= 0.85) & (sub.dist >= 0.30) & (sub.dist <= 0.80) & (sub.close >= sub.high20 * 0.995)],
            ["pullback", "breakout"], default="watch")
        sub["r20_rank"] = sub.trade_date.map(r20r[th])
        sub["r5_rank"] = sub.trade_date.map(r5r[th])
        sub["ex5"] = sub.f5 - sub.groupby("trade_date").f5.transform("mean")
        sub["ex10"] = sub.f10 - sub.groupby("trade_date").f10.transform("mean")
        elig = sub[(sub.r20_rank <= 5) & (sub.r5_rank <= 5) & (sub.role.isin(["leader", "second"]))
                   & (sub["mode"] == "breakout") & (sub.amt20_pct >= 0.70) & (sub.volr <= 1.5)].copy()
        if elig.empty:
            continue
        elig["theme"] = th
        elig["theme_score"] = [theme_score(a, b) for a, b in zip(elig.r20_rank, elig.r5_rank)]
        elig["role_score"] = elig.role.map({"leader": 1.0, "second": 0.75})
        elig["entry_q"] = 0.5 * elig.volr_pct + 0.3 * np.minimum(1, (elig.close / elig.high20 - 1) / 0.03) + 0.2 * np.minimum(1, elig.dist / 0.6)
        elig["score"] = 0.45 * elig.role_score + 0.30 * elig.theme_score + 0.25 * elig.entry_q
        elig = elig.join(idx_map, on="trade_date")
        elig["rank"] = elig.groupby("trade_date")["score"].rank(ascending=False, method="first")
        all_elig.append(elig)
        picks.append(elig[elig["rank"] <= TOPN])
    if not picks:
        print("no picks")
        return
    ea = pd.concat(all_elig, ignore_index=True).sort_values(["trade_date", "score"], ascending=[True, False]).drop_duplicates(["trade_date", "ts_code"])
    p = pd.concat(picks, ignore_index=True)
    # 每日去重（同一票可能落在多个主题）
    p = p.sort_values(["trade_date", "score"], ascending=[True, False]).drop_duplicates(["trade_date", "ts_code"])
    daily5 = p.groupby("trade_date").ex5.mean()
    daily10 = p.groupby("trade_date").ex10.mean()
    summary = {
        "n_picks": int(len(p)), "n_days": int(p.trade_date.nunique()),
        "ex5": stat(p.ex5), "ex10": stat(p.ex10),
        "week_t5": week_t(daily5.index, daily5.values), "week_t10": week_t(daily10.index, daily10.values),
    }
    by_month = {m: {"n": int(len(g)), "ex5": stat(g.ex5), "ex10": stat(g.ex10)}
                for m, g in p.groupby(p.trade_date.str[:6])}
    by_role = {r: {"n": int(len(g)), "ex5": stat(g.ex5), "ex10": stat(g.ex10)} for r, g in p.groupby("role")}
    by_theme = {t: {"n": int(len(g)), "ex5": stat(g.ex5), "ex10": stat(g.ex10)} for t, g in p.groupby("theme")}
    # 过滤后全集 vs topN：检验“排序”是否在破坏 edge
    all_summary = {"n": int(len(ea)), "ex5": stat(ea.ex5), "ex10": stat(ea.ex10)}
    ea = ea.copy()
    ea["score_q"] = pd.qcut(ea.score.rank(method="first"), 5, labels=False) + 1
    score_q = {int(q): {"n": int(len(g)), "ex5": stat(g.ex5), "ex10": stat(g.ex10)} for q, g in ea.groupby("score_q")}
    ea["volr_q"] = pd.qcut(ea.volr_pct.rank(method="first"), 5, labels=False) + 1
    volr_q = {int(q): {"n": int(len(g)), "ex5": stat(g.ex5), "ex10": stat(g.ex10)} for q, g in ea.groupby("volr_q")}
    variants = {}
    for name, mask in {
        "V0_当前": pd.Series(True, index=ea.index),
        "V1_指数r5>=0": ea.idx_r5 >= 0,
        "V2_量比中段0.4-0.8": ea.volr_pct.between(0.40, 0.80),
        "V3_V1+V2": (ea.idx_r5 >= 0) & ea.volr_pct.between(0.40, 0.80),
        "V4_指数站上MA20": ea.idx_above_ma20.fillna(False),
    }.items():
        g = ea[mask]
        daily = g.groupby("trade_date").ex5.mean()
        variants[name] = {"n": int(len(g)), "days": int(g.trade_date.nunique()),
                          "ex5": stat(g.ex5), "ex10": stat(g.ex10),
                          "week_t5": week_t(daily.index, daily.values)}
    out = {"meta": {"start": START, "end": END, "topn": TOPN, "n_picks": len(p)},
           "summary": summary, "all_eligible": all_summary, "score_q": score_q, "volr_q": volr_q,
           "variants": variants, "by_month": by_month, "by_role": by_role, "by_theme": by_theme}
    json.dump(out, open("data/eval_select_v0.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("all_eligible", json.dumps(all_summary, ensure_ascii=False))
    print("score_q", json.dumps(score_q, ensure_ascii=False))
    print("volr_q", json.dumps(volr_q, ensure_ascii=False))
    print("variants", json.dumps(variants, ensure_ascii=False, indent=1))
    print("by_role", json.dumps(by_role, ensure_ascii=False))
    print("by_month", json.dumps(by_month, ensure_ascii=False))
    print("by_theme", json.dumps(by_theme, ensure_ascii=False))


if __name__ == "__main__":
    main()
