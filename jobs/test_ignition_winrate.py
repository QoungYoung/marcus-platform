# -*- coding: utf-8 -*-
"""启动候选胜率：首次进前3 + 放量 + 突破 + 不延伸；对照全样本。"""
from __future__ import annotations
import os,sys,json
import numpy as np,pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS={
"storage":{'603986.SH':'兆易创新','300223.SZ':'北京君正','688008.SH':'澜起科技','301308.SZ':'江波龙','688525.SH':'佰维存储','001309.SZ':'德明利','300475.SZ':'香农芯创','000021.SZ':'深科技','600667.SH':'太极实业','688123.SH':'聚辰股份','688110.SH':'东芯股份','688766.SH':'普冉股份'},
"cpo":{'300308.SZ':'中际旭创','300502.SZ':'新易盛','300394.SZ':'天孚通信','002281.SZ':'光迅科技','000988.SZ':'华工科技','300570.SZ':'太辰光','300620.SZ':'光库科技','301205.SZ':'联特科技','688498.SH':'源杰科技','688048.SH':'长光华芯','603083.SH':'剑桥科技','601869.SH':'长飞光纤','600487.SH':'亨通光电','600522.SH':'中天科技'},
}
def run(g):
 names=GROUPS[g]
 b=pd.read_parquet(os.path.join(ROOT,'data/bars_api.parquet'),columns=['ts_code','trade_date','close','high','amount']); b=b[b.ts_code.isin(names)]
 p=b.pivot_table(index='trade_date',columns='ts_code',values='close',aggfunc='last').sort_index().loc['20250101':]
 h=b.pivot_table(index='trade_date',columns='ts_code',values='high',aggfunc='last').sort_index().loc['20250101':]
 amt=b.pivot_table(index='trade_date',columns='ts_code',values='amount',aggfunc='last').sort_index().loc['20250101':]
 r5=p.pct_change(5); ma20=p.rolling(20).mean(); hi20=h.rolling(20).max(); amtr=amt.rolling(5).mean()/amt.rolling(20).mean(); rank=r5.rank(axis=1,ascending=False,method='min')
 idx={d:i for i,d in enumerate(p.index)}; rows=[]; base=[]
 for d in p.index:
  i=idx[d]
  if i<21 or i+21>=len(p.index): continue
  for c in names:
   if c not in p.columns: continue
   f5=float(p[c].iloc[i+6]/p[c].iloc[i+1]-1); f10=float(p[c].iloc[i+11]/p[c].iloc[i+1]-1); f20=float(p[c].iloc[i+21]/p[c].iloc[i+1]-1)
   base.append((d,c,f5,f10,f20))
   prev=rank.shift(1).loc[d,c]; now=rank.loc[d,c]
   first=pd.notna(now) and now<=3 and (pd.isna(prev) or prev>5)
   vol=pd.notna(amtr.loc[d,c]) and amtr.loc[d,c]>=1.2
   brk=pd.notna(hi20.shift(1).loc[d,c]) and p.loc[d,c]>=hi20.shift(1).loc[d,c]*0.98
   ext=pd.notna(ma20.loc[d,c]) and p.loc[d,c]<=ma20.loc[d,c]*1.15
   if first and vol and brk and ext: rows.append((d,c,f5,f10,f20))
 def st(x):
  x=pd.DataFrame(x,columns=['date','code','f5','f10','f20'])
  return {'n':len(x),'f5_mean':round(float(x.f5.mean())*100,2),'f5_win':round(float((x.f5>0).mean()),3),'f10_mean':round(float(x.f10.mean())*100,2),'f10_win':round(float((x.f10>0).mean()),3),'f20_mean':round(float(x.f20.mean())*100,2),'f20_win':round(float((x.f20>0).mean()),3)} if len(x) else {'n':0}
 return st(rows),st(base)
for g in GROUPS:
 a,b=run(g); print(g,json.dumps({'ignition':a,'baseline':b},ensure_ascii=False))
