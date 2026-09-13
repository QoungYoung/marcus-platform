# -*- coding: utf-8 -*-
import json, sys
sys.path.insert(0,"/app")
import pandas as pd, numpy as np
hist=json.load(open("/app/data/concept_hist.json",encoding="utf-8"))
def ser(code):
    idx=pd.to_datetime(hist[code]["dates"]); return pd.Series(hist[code]["close"],index=idx)
def pos(s,upto):
    s=s[s.index<=pd.Timestamp(upto)]
    if len(s)<30 or s.isna().any(): return None
    c=float(s.iloc[-1]); hi=float(s.iloc[-250:].max()); box=s.iloc[-30:]; bhi,blo=float(box.max()),float(box.min()); rng=bhi-blo or 1
    ma60=float(s.iloc[-60:].mean()) if len(s)>=60 else float(s.mean())
    vh=(c/hi-1)*100; bp=(c-blo)/rng*100; vm=(c/ma60-1)*100
    m60a=float(s.iloc[-60:].mean()) if len(s)>=60 else float(s.mean()); m60b=float(s.iloc[-30:].mean()) if len(s)>=30 else float(s.mean())
    slope=(m60a-m60b)/abs(m60b or 1)
    trend="UP" if (slope>0.3 and vm>0) else ("DOWN" if (slope<-0.3 and vm<0) else "ADJUST")
    if vh<=-30 and bp<=35 and vm<0: return "LOW"
    if (vh>=-10 and bp>=65 and vm>0) or (bp>=80 and vm>0 and trend=="UP" and vh>=-20): return "HIGH"
    return "MID"
def fwd(s,upto,n):
    base=s[s.index<=pd.Timestamp(upto)]; fut=s[s.index>pd.Timestamp(upto)]
    if len(base)<1 or len(fut)<n or base.isna().any() or fut.iloc[:n].isna().any(): return None
    return round((float(fut.iloc[n-1])/float(base.iloc[-1])-1)*100,1)
for dt,n in [("2025-12-01",20),("2026-01-12",20),("2026-03-16",20),("2026-05-13",20)]:
    groups={}
    for code in hist:
        s=ser(code); p=pos(s,dt)
        if p is None: continue
        r=fwd(s,dt,n)
        if r is None: continue
        groups.setdefault(p,[]).append(r)
    print("==",dt)
    for p in ["HIGH","MID","LOW"]:
        v=[x for x in groups.get(p,[]) if x==x]
        if v: print(f"  {p:5} n={len(v):3} 平均20日={np.mean(v):6.2f}% 中位={np.median(v):6.2f}%")
