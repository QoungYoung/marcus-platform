# -*- coding: utf-8 -*-
"""在 588170 上扫描左侧恐慌低点 / 右侧确认，评估 B反阶段识别。"""
from __future__ import annotations
import json,os,sys
import numpy as np,pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,ROOT)
from core.tushare_relay import relay_query
OUT=os.path.join(ROOT,'data/core_pit')

def load():
 d=relay_query('fund_daily',ts_code='588170.SH',start_date='20250101',end_date='20261021').sort_values('trade_date').reset_index(drop=True)
 for c in ['close','high','low','amount','pct_chg']: d[c]=pd.to_numeric(d[c],errors='coerce')
 d['ma5']=d.close.rolling(5).mean(); d['ma20']=d.close.rolling(20).mean()
 d['lo20']=d.low.rolling(20).min(); d['hi20']=d.high.rolling(20).max(); d['a20']=d.amount.rolling(20).mean()
 d['pos']=(d.close-d.low)/(d.high-d.low)
 d['r5']=d.close/d.close.shift(5)-1; d['r20']=d.close/d.close.shift(20)-1
 return d

def fwd(d,i,h):
 j=i+h
 return None if j>=len(d) else float(d.close.iloc[j]/d.close.iloc[i]-1)*100

def main():
 d=load(); rows=[]
 for i in range(25,len(d)-20):
  r=d.iloc[i]
  left=(r.pct_chg<=-5 and r.close<=r.lo20*1.02 and r.amount>=r.a20*1.2 and r.pos<=0.25 and r.close<r.ma20*0.95)
  right=(r.close>r.ma5 and r.pos>=0.5 and r.r5>0 and r.close>=d.high.iloc[i-5:i].max()*0.995 and r.amount>=r.a20*0.8)
  if left or right:
   rows.append({'date':r.trade_date,'kind':'left_low' if left else 'right_confirm','ret1':round(r.pct_chg,2),
                'r5':round(r.r5*100,1),'pos':round(r.pos,2),'amt':round(r.amount/r.a20,2) if r.a20 else None,
                'ma20':round(r.close/r.ma20-1,3),'f5':round(fwd(d,i,5),2) if fwd(d,i,5) is not None else None,
                'f10':round(fwd(d,i,10),2) if fwd(d,i,10) is not None else None,
                'f20':round(fwd(d,i,20),2) if fwd(d,i,20) is not None else None})
 df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,'b_rebound_rules.csv'),index=False,encoding='utf-8-sig')
 print(df.to_string(index=False))
 for k in ['left_low','right_confirm']:
  x=df[df.kind==k]
  if len(x): print(k,'n',len(x),'f5',round(x.f5.mean(),2),'f20',round(x.f20.mean(),2),'win5',round((x.f5>0).mean(),2))
 json.dump({'n':len(df),'by_kind':df.kind.value_counts().to_dict(),
            'stats':{k:{'n':int(len(df[df.kind==k])),'f5':round(float(df[df.kind==k].f5.mean()),2),'f20':round(float(df[df.kind==k].f20.mean()),2)} for k in df.kind.unique()}},
           open(os.path.join(OUT,'b_rebound_rules_summary.json'),'w'),ensure_ascii=False,indent=1)
if __name__=='__main__': main()
