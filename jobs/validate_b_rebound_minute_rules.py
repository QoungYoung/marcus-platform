# -*- coding: utf-8 -*-
"""分钟级 B反 低点规则：precision/recall 弱标签验证。"""
from __future__ import annotations
import json,os
import pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
df=pd.read_csv(os.path.join(ROOT,'data/core_pit/b_rebound_minute_candidates.csv'),dtype={'date':str})
def evaluate(name,fn):
 tp=[]; fp=[]; fn_=[]
 for _,r in df.iterrows():
  hit=bool(fn(r)); pos=r.label=='pos'
  if hit and pos: tp.append(r.date)
  elif hit and not pos: fp.append(r.date)
  elif not hit and pos: fn_.append(r.date)
 print(name,'TP',len(tp),'FP',len(fp),'FN',len(fn_),'precision',round(len(tp)/max(len(tp)+len(fp),1),2),'recall',round(len(tp)/6,2))
 print('  TP',tp,'FP',fp)
 return {'tp':tp,'fp':fp,'fn':fn_}
rules={
 'rebound_confirm':lambda r: pd.notna(r.low_to_30m) and r.low_to_30m>=1.5 and pd.notna(r.close_pos) and r.close_pos>=0.5,
 'shrink_stop':lambda r: r.low_vol_ratio<=1.0 and pd.notna(r.close_pos) and r.close_pos>=0.8 and pd.notna(r.low_to_30m) and r.low_to_30m>=1.0,
 'panic_late':lambda r: str(r.low_time)>='14:00' and r.low_vol_ratio>=1.5 and pd.notna(r.close_pos) and r.close_pos<=0.1,
}
out={k:evaluate(k,v) for k,v in rules.items()}
json.dump(out,open(os.path.join(ROOT,'data/core_pit/b_rebound_minute_rules.json'),'w'),ensure_ascii=False,indent=1)
