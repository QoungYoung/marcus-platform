# -*- coding: utf-8 -*-
"""core PIT 回测：PIT chain_map 标签 + PIT 主题成员，20 日持有。

比较：
  A rule      : stage×role 选股等权
  B overlay   : 同一批 picks，按 PIT 龙头状态加仓位乘数
  C theme_eq  : PIT 主题成员等权
"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "data/pit/chain_map/labels_weekly.csv")
OBS = os.path.join(ROOT, "data/pit/theme_members/observations.csv")
OUT = os.path.join(ROOT, "data/core_pit")
os.makedirs(OUT, exist_ok=True)

def stage_of(members, close, d):
    # PIT 成员等权 r5/r20 + 广度；不足则 unknown
    codes = [c for c in members if c in close.columns]
    if len(codes) < 3: return "unknown", {}
    s = close.loc[:d, codes].tail(61)
    if len(s) < 21: return "unknown", {}
    r5 = float((s.iloc[-1] / s.iloc[-6] - 1).mean())
    r20 = float((s.iloc[-1] / s.iloc[-21] - 1).mean())
    ma20 = s.rolling(20).mean().iloc[-1]
    b20 = float((s.iloc[-1] > ma20).mean())
    if r20 < -0.08 and b20 < 0.2: st = "oversold"
    elif r20 < 0 and b20 < 0.3 and abs(r5) < 0.03: st = "low_flat"
    elif r20 < 0 and r5 < 0: st = "downtrend"
    elif r20 < 0 and r5 > 0: st = "weak"
    elif r20 > 0.08 and (r5 < 0 or b20 < 0.5): st = "extended"
    else: st = "strong"
    return st, {"r5": r5, "r20": r20, "breadth20": b20}

def leader_state(lab_theme, close, d):
    x = lab_theme[(lab_theme["rank"] == 1)]
    if not len(x): return "unknown"
    code = str(x.iloc[0].ts_code)
    s = close[code].loc[:d].dropna()
    if len(s) < 21: return "unknown"
    ma20 = s.iloc[-20:].mean(); low20 = s.iloc[-21:-1].min()
    if s.iloc[-1] < low20: return "dead"
    if s.iloc[-1] < ma20: return "warning"
    return "alive"

def main():
    lab = pd.read_csv(LAB, dtype={"date": str, "ts_code": str})
    obs = pd.read_csv(OBS, dtype={"as_of_date": str, "ts_code": str})
    bars = pd.read_parquet(os.path.join(ROOT, "data/bars_combined.parquet"),
                           columns=["ts_code", "trade_date", "close"])
    close = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    dates = sorted(lab.date.unique())
    idx = {str(d): i for i, d in enumerate(close.index)}
    picks, themes = [], []
    for d in dates:
        if d not in idx: continue
        i = idx[d]
        if i + 21 >= len(close.index): continue
        day = lab[lab.date == d]
        for theme, g in day.groupby("theme"):
            m = obs[(obs.as_of_date == d) & (obs.theme == theme)].ts_code.dropna().unique().tolist()
            if not m: m = g.ts_code.dropna().unique().tolist()
            st, meta = stage_of(m, close, d)
            if st in ("low_flat", "oversold", "unknown"): continue
            if st in ("strong", "extended"):
                cand = g[(g.is_second == 1) | (g["rank"].isin([2, 3, 4]))]
            else:
                cand = g[g["rank"] == 1]
            if not len(cand): cand = g[g["rank"] == 1]
            cand = cand.drop_duplicates("ts_code").head(2)
            ls = leader_state(g, close, d)
            mult = {"alive": 1.0, "warning": 0.5, "dead": 0.25, "unknown": 0.5}.get(ls, 0.5)
            for _, p in cand.iterrows():
                c = str(p.ts_code)
                if c not in close.columns: continue
                try:
                    e = close[c].iloc[i + 1]; f = close[c].iloc[i + 1 + 20]
                    if pd.isna(e) or pd.isna(f) or e <= 0: continue
                    r = float(f / e - 1)
                except Exception:
                    continue
                picks.append({"date": d, "theme": theme, "code": c, "name": p.get("name"),
                              "stage": st, "role": "second" if p.get("is_second") == 1 else "leader",
                              "leader_state": ls, "mult": mult, "f20": r})
            try:
                mm = [c for c in m if c in close.columns]
                e = close[mm].iloc[i + 1]; f = close[mm].iloc[i + 1 + 20]
                rr = (f / e - 1).dropna()
                if len(rr): themes.append({"date": d, "theme": theme, "stage": st, "f20": float(rr.mean())})
            except Exception:
                pass
    df = pd.DataFrame(picks); td = pd.DataFrame(themes)
    df.to_csv(os.path.join(OUT, "picks.csv"), index=False, encoding="utf-8-sig")
    td.to_csv(os.path.join(OUT, "themes.csv"), index=False, encoding="utf-8-sig")
    def stat(x):
        x = pd.Series(x).dropna()
        if len(x) < 3: return {"n": len(x), "mean": None, "win": None, "t": None}
        return {"n": int(len(x)), "mean": round(float(x.mean()) * 100, 3),
                "win": round(float((x > 0).mean()), 3),
                "t": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2)}
    # 日期组合
    daily = df.groupby("date").apply(lambda g: pd.Series({
        "A": g.f20.mean(),
        "B": np.average(g.f20, weights=g.mult),
        "n": len(g)})).reset_index()
    daily = daily.merge(td.groupby("date").f20.mean().rename("C"), on="date", how="left")
    for c in ["A","B","C"]:
        print(c, stat(daily[c]))
    mid = sorted(daily.date)[len(daily)//2]
    for c in ["A","B","C"]:
        h1 = daily[daily.date <= mid][c]; h2 = daily[daily.date > mid][c]
        print(c, "H1", stat(h1), "H2", stat(h2))
    print("picks", len(df), "dates", df.date.nunique())
    print(df.groupby("stage").f20.agg(["count","mean"]).to_string())
    json.dump({"A": stat(daily.A), "B": stat(daily.B), "C": stat(daily.C),
               "n_dates": len(daily), "n_picks": len(df),
               "mid": mid, "H1": {c: stat(daily[daily.date <= mid][c]) for c in ["A","B","C"]},
               "H2": {c: stat(daily[daily.date > mid][c]) for c in ["A","B","C"]}},
              open(os.path.join(OUT, "result.json"), "w"), ensure_ascii=False, indent=1)
    print(daily.tail().to_string(index=False))

if __name__ == "__main__":
    main()
