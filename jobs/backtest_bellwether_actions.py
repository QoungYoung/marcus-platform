# -*- coding: utf-8 -*-
"""动作级回测：不选股，只看风向标状态下做“二线/高切低”的 5 日结果。"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
from backtest_core_pit import build_states, state_at, LAB, OBS

OUT = os.path.join(ROOT, "data/core_pit")
bars = pd.read_parquet(os.path.join(ROOT, "data/bars_api.parquet"),
                       columns=["ts_code","trade_date","close","high","amount"])
close = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
high = bars.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
lab = pd.read_csv(LAB, dtype={"date": str, "ts_code": str})
obs = pd.read_csv(OBS, dtype={"as_of_date": str, "ts_code": str})
build_states(lab, obs, close, high, amt)
idx = {str(d): i for i, d in enumerate(close.index)}

def f5(code, i):
    j = i + 1 + 5
    if j >= len(close.index) or code not in close.columns: return None
    e = close[code].iloc[i+1]; f = close[code].iloc[j]
    return float(f/e-1) if pd.notna(e) and pd.notna(f) and e>0 else None

rows=[]
for d in sorted(lab.date.unique()):
    if d not in idx: continue
    i=idx[d]
    for theme,g in lab[lab.date==d].groupby("theme"):
        st=state_at(theme,d)
        sec=g[g.bucket=="second"]; lead=g[g["rank"]==1]
        if not len(sec) or not len(lead): continue
        sr=[f5(str(c),i) for c in sec.ts_code]; sr=[x for x in sr if x is not None]
        lr=[f5(str(c),i) for c in lead.ts_code]; lr=[x for x in lr if x is not None]
        if not sr or not lr: continue
        rows.append({"date":d,"theme":theme,"state":st,
                     "second_ret":float(np.mean(sr)),
                     "leader_ret":float(np.mean(lr)),
                     "high_cut_low":float(np.mean(sr)-np.mean(lr)),
                     "n_second":len(sr),"n_leader":len(lr)})
df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,"actions_5d.csv"),index=False,encoding="utf-8-sig")
def stat(x):
    x=pd.Series(x).dropna(); 
    return {"n":int(len(x)),"mean":round(float(x.mean())*100,3),"win":round(float((x>0).mean()),3),
            "t":round(float(x.mean()/(x.std(ddof=1)/np.sqrt(len(x)))),2) if len(x)>2 and x.std(ddof=1)>0 else None}
out={"n_dates":int(df.date.nunique()),"n_theme_days":len(df),
     "state":{},"stage":{}}
for st,g in df.groupby("state"):
    out["state"][st]={"second":stat(g.second_ret),"leader":stat(g.leader_ret),
                      "high_cut_low":stat(g.high_cut_low)}
json.dump(out,open(os.path.join(OUT,"actions_5d.json"),"w"),ensure_ascii=False,indent=1)
print(json.dumps(out,ensure_ascii=False,indent=1))
