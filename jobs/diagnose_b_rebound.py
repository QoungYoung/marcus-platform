# -*- coding: utf-8 -*-
"""分离 B反 标记问题 vs 选龙问题：指数 fwd vs 老龙头/当期龙头 fwd。"""
from __future__ import annotations
import json, os
import numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
leads=pd.read_csv(os.path.join(ROOT,'data/core_pit/b_rebound_leaders.csv'),dtype={'start':str})
ix=json.load(open(os.path.join(ROOT,'data/index_daily_000001_SH.json'),encoding='utf-8'))['rows']
ix=pd.DataFrame(ix,columns=['date','close','high','amount']).set_index('date').sort_index()
rows=[]
for s,g in leads.groupby('start'):
 if s not in ix.index: continue
 i=list(ix.index).index(s)
 def f(h):
  j=i+1+h
  return float(ix.close.iloc[j]/ix.close.iloc[i+1]-1) if j<len(ix) else np.nan
 old=g[g.kind=='old_low']; cur=g[g.kind=='current']
 rows.append({'start':s,'idx_f5':f(5),'idx_f10':f(10),'idx_f20':f(20),
              'old_f5':old.f5.mean(),'old_f10':old.f10.mean(),'old_f20':old.f20.mean(),
              'cur_f5':cur.f5.mean(),'cur_f10':cur.f10.mean(),'cur_f20':cur.f20.mean(),
              'n_old':len(old),'n_cur':len(cur)})
df=pd.DataFrame(rows)
for c in df.columns:
 if c.startswith(('idx','old','cur')): df[c]=(df[c]*100).round(2)
print(df.to_string(index=False))
df.to_csv(os.path.join(ROOT,'data/core_pit/b_rebound_diagnosis.csv'),index=False,encoding='utf-8-sig')
print('\nmean'); print(df[['idx_f5','old_f5','cur_f5','idx_f20','old_f20','cur_f20']].mean().round(2).to_string())
