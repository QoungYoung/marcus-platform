# -*- coding: utf-8 -*-
"""dead 事件研究：只看 episode onset，加“反弹失败确认”，看相对收益/尾部。"""
from __future__ import annotations
import json, os
import numpy as np, pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data/core_pit")
ev = pd.read_csv(os.path.join(OUT, "bellwether_events.csv"), dtype={"date": str, "leader": str})
bars = pd.read_parquet(os.path.join(ROOT, "data/bars_api.parquet"), columns=["ts_code","trade_date","close"])
close = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
idx = {str(d): i for i, d in enumerate(close.index)}

def fwd(code, i, h=5):
    j=i+1+h
    if j >= len(close.index) or code not in close.columns: return None
    e=close[code].iloc[i+1]; f=close[code].iloc[j]
    return float(f/e-1) if pd.notna(e) and pd.notna(f) and e>0 else None

dead = ev[ev.state=="dead"].dropna(subset=["dead_episode"])
rows=[]
for (theme, seg, ep), g in dead.groupby(["theme","segment_role","dead_episode"]):
    g=g.sort_values("date"); r=g.iloc[0]; d=str(r.date)
    if d not in idx: continue
    i=idx[d]; high=float(r.last_high) if pd.notna(r.last_high) else None
    # T+1..T+3 是否收回 last_high 的 98%
    rebound=False; check=[]
    for k in range(1,4):
        j=i+k
        if j < len(close.index):
            c=close[r.leader].iloc[j] if r.leader in close.columns else np.nan
            check.append(None if pd.isna(c) else round(float(c),2))
            if high and pd.notna(c) and c >= high*0.98: rebound=True
    secs=[x for x in str(r.second_codes or "").split(",") if x]
    sr=[fwd(x,i) for x in secs]; sr=[x for x in sr if x is not None]
    lr=fwd(str(r.leader),i)
    if not sr or lr is None: continue
    rows.append({"episode":int(ep),"date":d,"theme":theme,"segment_role":seg,"leader":r.leader,
                 "last_high":high,"rebound":rebound,"next3":check,
                 "confirmed_dead":not rebound,
                 "second_ret":float(np.mean(sr)),"leader_ret":lr,
                 "hcl":float(np.mean(sr)-lr),"second_min":float(np.min(sr))})
df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,"dead_episodes.csv"),index=False,encoding="utf-8-sig")
def st(x):
    x=pd.Series(x).dropna();
    if not len(x): return {"n":0}
    return {"n":int(len(x)),"mean":round(float(x.mean())*100,2),"min":round(float(x.min())*100,2),"win":round(float((x>0).mean()),3)}
out={"n_episodes":len(df),"confirmed":int(df.confirmed_dead.sum()),"rebound":int((~df.confirmed_dead).sum()),
     "confirmed_stats":{"second":st(df[df.confirmed_dead].second_ret),"hcl":st(df[df.confirmed_dead].hcl),
                        "second_min":st(df[df.confirmed_dead].second_min)},
     "rebound_stats":{"second":st(df[~df.confirmed_dead].second_ret),"hcl":st(df[~df.confirmed_dead].hcl)}}
json.dump(out,open(os.path.join(OUT,"dead_episode_analysis.json"),"w"),ensure_ascii=False,indent=1)
print(json.dumps(out,ensure_ascii=False,indent=1))
