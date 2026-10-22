# -*- coding: utf-8 -*-
"""用未来 5 日实际结果评估 AI/机械状态，不比较两者是否一致。"""
from __future__ import annotations
import argparse, json, os
import numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT=os.path.join(ROOT,"data/core_pit")
LAB=os.path.join(ROOT,"data/pit/chain_map/labels_weekly.csv")
BARS=os.path.join(ROOT,"data/bars_api.parquet")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--cases",default=os.path.join(OUT,"ai_validation_cases_v4.csv")); a=ap.parse_args()
    d=pd.read_csv(a.cases,dtype={"date":str}); lab=pd.read_csv(LAB,dtype={"date":str,"ts_code":str})
    bars=pd.read_parquet(BARS,columns=["ts_code","trade_date","close"]); close=bars.pivot_table(index="trade_date",columns="ts_code",values="close",aggfunc="last").sort_index()
    idx={str(x):i for i,x in enumerate(close.index)}
    rows=[]
    for _,r in d.iterrows():
        if r.ai=="ERROR": continue
        i=idx.get(str(r.date));
        if i is None or i+6>=len(close.index): continue
        g=lab[(lab.date==r.date)&(lab.theme==r.theme)&(lab.segment_role==r.segment)]
        lead=g[g["rank"]==1].drop_duplicates("ts_code")
        sec=g[g.bucket=="second"].drop_duplicates("ts_code")
        if not len(lead): continue
        def f5(code):
            j=i+6
            if code not in close.columns: return None
            e=close[code].iloc[i+1]; f=close[code].iloc[j]
            return float(f/e-1) if pd.notna(e) and pd.notna(f) and e>0 else None
        lr=f5(str(lead.iloc[0].ts_code)); sr=[f5(str(x)) for x in sec.ts_code]; sr=[x for x in sr if x is not None]
        if lr is None or not sr: continue
        rows.append({"theme":r.theme,"segment":r.segment,"date":r.date,"mechanical":r.mechanical,"ai":r.ai,
                     "second":float(np.mean(sr)),"leader":lr,"hcl":float(np.mean(sr)-lr)})
    df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,"ai_realized_5d.csv"),index=False,encoding="utf-8-sig")
    def st(x):
        x=pd.Series(x).dropna()
        if not len(x): return {"n":0}
        return {"n":int(len(x)),"mean":round(float(x.mean())*100,2),"median":round(float(x.median())*100,2),
                "min":round(float(x.min())*100,2),"win":round(float((x>0).mean()),3)}
    out={"n":len(df),"by_ai":{},"by_mechanical":{}}
    for stcol in ["ai","mechanical"]:
        for k,g in df.groupby(stcol):
            out["by_"+stcol][k]={"second":st(g.second),"hcl":st(g.hcl)}
    json.dump(out,open(os.path.join(OUT,"ai_realized_5d.json"),"w"),ensure_ascii=False,indent=1)
    print(json.dumps(out,ensure_ascii=False,indent=1))
if __name__=="__main__": main()
