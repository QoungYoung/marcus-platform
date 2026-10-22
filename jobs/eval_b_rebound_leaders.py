# -*- coding: utf-8 -*-
"""B反阶段：历史老龙头低吸 vs 当期龙头的后续收益。研究代理。"""
from __future__ import annotations
import os,sys,json
import numpy as np,pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB=os.path.join(ROOT,'data/pit/chain_map/labels_weekly.csv')
BARS=os.path.join(ROOT,'data/bars_api.parquet')
STARTS=['20250410','20251230','20260225','20260615','20260827']

lab=pd.read_csv(LAB,dtype={'date':str,'ts_code':str})
b=pd.read_parquet(BARS,columns=['ts_code','trade_date','close']); p=b.pivot_table(index='trade_date',columns='ts_code',values='close',aggfunc='last').sort_index(); idx={d:i for i,d in enumerate(p.index)}
rows=[]
for s in STARTS:
 if s not in p.index: continue
 i=idx[s]
 prior=lab[lab.date<=s]
 recent=prior[prior.date>= (prior.date.max() if False else '20250101')]
 # old leaders: rank1 in prior 120 calendar days; use unique codes per theme/seg
 cutoff=(pd.to_datetime(s)-pd.Timedelta(days=120)).strftime('%Y%m%d')
 old=prior[(prior.date>=cutoff)&(prior['rank']==1)]
 for (theme,seg),g in old.groupby(['theme','segment_role']):
  codes=sorted(set(g.ts_code.astype(str)))
  for c in codes:
   if c not in p.columns: continue
   hist=p[c].loc[:s].dropna().tail(120)
   if len(hist)<21: continue
   dd=float(hist.iloc[-1]/hist.max()-1)
   if dd > -0.30: continue
   def fwd(h):
    j=i+1+h
    if j>=len(p.index): return None
    e=p[c].iloc[i+1]; f=p[c].iloc[j]; return float(f/e-1) if pd.notna(e) and pd.notna(f) and e>0 else None
   rows.append({'start':s,'theme':theme,'segment':seg,'code':c,'dd120':round(dd,3),
                'f5':fwd(5),'f10':fwd(10),'f20':fwd(20),'kind':'old_low'})
 # current leaders
 cur=prior[prior.date==prior.date.max()]
 for _,r in cur[cur['rank']==1].iterrows():
  c=str(r.ts_code)
  if c not in p.columns: continue
  def fwd(h):
   j=i+1+h
   if j>=len(p.index): return None
   e=p[c].iloc[i+1]; f=p[c].iloc[j]; return float(f/e-1) if pd.notna(e) and pd.notna(f) and e>0 else None
  rows.append({'start':s,'theme':r.theme,'segment':r.segment_role,'code':c,'dd120':None,'f5':fwd(5),'f10':fwd(10),'f20':fwd(20),'kind':'current'})
df=pd.DataFrame(rows); df.to_csv(os.path.join(ROOT,'data/core_pit/b_rebound_leaders.csv'),index=False,encoding='utf-8-sig')
print(df.groupby('kind')[['f5','f10','f20']].agg(['count','mean']).round(4).to_string())
print(df[df.kind=='old_low'].to_string(index=False))
