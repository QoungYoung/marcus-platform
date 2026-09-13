# -*- coding: utf-8 -*-
import json, sys
sys.path.insert(0,"/app/apps/main_line")
import pandas as pd, numpy as np
import position_class as pc
hist=json.load(open("/app/data/concept_hist.json",encoding="utf-8"))
def ser(code):
    idx=pd.to_datetime(hist[code]["dates"]); return pd.Series(hist[code]["close"],index=idx)
def fwd(s,upto,n):
    base=s[s.index<=pd.Timestamp(upto)]; fut=s[s.index>pd.Timestamp(upto)]
    if len(base)<1 or len(fut)<n or base.isna().any() or fut.iloc[:n].isna().any(): return None
    return round((float(fut.iloc[n-1])/float(base.iloc[-1])-1)*100,1)
def fund_at(code,upto):
    idx=pd.to_datetime(hist[code]["dates"]); s=pd.Series(hist[code]["net_amount"],index=idx)[lambda x:x.index<=pd.Timestamp(upto)]
    last=s.iloc[-1] if len(s) else 0
    return {"dir":"in" if last>0 else ("out" if last<0 else "flat"),"strength":round(float(last)/1e8,1)}
wave={"op":"t_only"}
for dt in ["2025-12-01","2026-01-12","2026-03-16","2026-05-13"]:
    gpos={}; gact={}
    for code in hist:
        s=ser(code); base=s[s.index<=pd.Timestamp(dt)]
        if len(base)<60 or float(base.min())<=0 or base.isna().any(): continue
        f=pc.position_features(base)
        if f is None: continue
        r=pc.classify(f); fs=fund_at(code,dt)
        act,sig=pc.resonance(r["position"],f,fs,1.0,None,wave["op"])
        rf=fwd(s,dt,20)
        if rf is None: continue
        gpos.setdefault(r["position"],[]).append(rf)
        gact.setdefault(act,[]).append(rf)
    print("==",dt)
    for k in ["HIGH","MID","LOW"]:
        v=[x for x in gpos.get(k,[]) if x==x]
        if v: print(f"  位置 {k:4} n={len(v):3} avg20={np.mean(v):6.2f}%")
    for k in ["防御清仓","减仓/只做T","观望(高位健康可持有)","低吸埋伏","观望"]:
        v=[x for x in gact.get(k,[]) if x==x]
        if v: print(f"  共振 {k:14} n={len(v):3} avg20={np.mean(v):6.2f}%")
