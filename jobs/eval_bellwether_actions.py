# -*- coding: utf-8 -*-
"""验证风向标的正确用途：补涨/二线、低切、持有，而非子主题收益。"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend")); sys.path.insert(0, os.path.join(ROOT, "backend/app/services"))
from wolf_card.subthemes import stocks as sub_stocks

OUT = os.path.join(ROOT, "data/core_p1")
h = pd.read_csv(os.path.join(OUT, "bellwether_history.csv"), dtype={"date": str})
h = h[h.impulse == True].copy()
bars = pd.read_parquet(os.path.join(ROOT, "data/bars_combined.parquet"), columns=["ts_code","trade_date","close","amount"])
piv = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
idx = {str(d): i for i, d in enumerate(piv.index)}
rows = []
for _, r in h.iterrows():
    d = r.date
    if d not in idx: continue
    i = idx[d]
    if i + 21 >= len(piv.index): continue
    codes = [c for c in sub_stocks(r.subtheme) if c in piv.columns]
    if len(codes) < 5: continue
    bell = r.bellwether_code
    # 二线：非风向标，20日成交额前2
    try:
        sec = [c for c in amt.loc[:d, codes].tail(20).mean().nlargest(6).index if c != bell][:2]
    except Exception:
        sec = []
    # 低切代理：非风向标，r20 最低 30%
    try:
        cl = piv.loc[:d, codes].iloc[-21]
        r20 = piv.loc[d, codes] / cl - 1
        low = [c for c in r20.sort_values().index[:max(2, len(codes)//4)] if c != bell]
    except Exception:
        low = []
    rec = {"date": d, "subtheme": r.subtheme, "state": r.state, "bell": bell}
    for name, cs in [("bell", [bell]), ("second", sec), ("low", low)]:
        for hz in [5, 10, 20]:
            if i + 1 + hz >= len(piv.index): continue
            e = piv.iloc[i+1][cs].astype(float); x = piv.iloc[i+1+hz][cs].astype(float)
            rr = (x / e - 1).dropna()
            rec[f"{name}_{hz}"] = float(rr.mean()) if len(rr) else None
    rows.append(rec)
df = pd.DataFrame(rows)
df.to_csv(os.path.join(OUT, "bellwether_action_events.csv"), index=False, encoding="utf-8-sig")
print("events", len(df))
for hz in [5,10,20]:
    print("\n== horizon", hz)
    for st in ["alive","warning","rotated","dead"]:
        sub = df[df.state==st]
        vals = {k: sub[f"{k}_{hz}"].dropna() for k in ["bell","second","low"]}
        print(st, "n", len(sub), {k: (round(v.mean()*100,2), round((v>0).mean(),2), len(v)) for k,v in vals.items()})
# alive vs warning differences
print("\n== alive - warning (pp)")
for hz in [5,10,20]:
    for k in ["bell","second","low"]:
        a = df[df.state=="alive"][f"{k}_{hz}"].dropna(); w = df[df.state=="warning"][f"{k}_{hz}"].dropna()
        if len(a)>5 and len(w)>5:
            print(hz,k,"alive",round(a.mean()*100,2),"warning",round(w.mean()*100,2),"diff",round((a.mean()-w.mean())*100,2))
