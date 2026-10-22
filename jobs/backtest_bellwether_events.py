# -*- coding: utf-8 -*-
"""事件级动作回测：补涨/高切低，按 state 分组；5日。"""
from __future__ import annotations
import json, os
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/core_pit")
ev = pd.read_csv(os.path.join(OUT, "bellwether_events.csv"), dtype={"date": str, "leader": str})
bars = pd.read_parquet(os.path.join(ROOT, "data/bars_api.parquet"), columns=["ts_code","trade_date","close"])
close = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
idx = {str(d): i for i, d in enumerate(close.index)}

def f5(code, i):
    j = i + 6
    if j >= len(close.index) or code not in close.columns: return None
    e = close[code].iloc[i+1]; f = close[code].iloc[j]
    return float(f/e-1) if pd.notna(e) and pd.notna(f) and e > 0 else None

rows=[]
for _, r in ev.iterrows():
    d=str(r.date)
    if d not in idx: continue
    i=idx[d]; secs=[x for x in str(r.second_codes or "").split(",") if x]
    sr=[f5(x,i) for x in secs]; sr=[x for x in sr if x is not None]
    lr=f5(str(r.leader),i)
    if not sr or lr is None: continue
    rows.append({"date":d,"theme":r.theme,"segment_role":r.segment_role,"state":r.state,
                 "second_ret":float(np.mean(sr)),"leader_ret":lr,
                 "high_cut_low":float(np.mean(sr)-lr),
                 "catchup_flag":bool(r.second_catchup),"hcl_flag":bool(r.high_cut_low)})
df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,"events_5d.csv"),index=False,encoding="utf-8-sig")
def stat(x):
    x=pd.Series(x).dropna()
    if not len(x): return {"n":0}
    return {"n":int(len(x)),"mean":round(float(x.mean())*100,3),"win":round(float((x>0).mean()),3),
            "t":round(float(x.mean()/(x.std(ddof=1)/np.sqrt(len(x)))),2) if len(x)>2 and x.std(ddof=1)>0 else None}
out={"n_rows":len(df),"by_state":{},"events":{}}
for st,g in df.groupby("state"):
    out["by_state"][st]={"second":stat(g.second_ret),"hcl":stat(g.high_cut_low)}
out["events"]={"catchup":stat(df[df.catchup_flag].second_ret),"hcl":stat(df[df.hcl_flag].high_cut_low)}
json.dump(out,open(os.path.join(OUT,"events_5d.json"),"w"),ensure_ascii=False,indent=1)
print(json.dumps(out,ensure_ascii=False,indent=1))
