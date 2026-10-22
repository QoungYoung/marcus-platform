# -*- coding: utf-8 -*-
"""B反低点候选：正样本 6 个 + 低区负样本，stk_mins 分钟特征。"""
from __future__ import annotations
import os,sys,json
import numpy as np,pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,ROOT)
from core.tushare_relay import relay_query
OUT=os.path.join(ROOT,'data/core_pit')
POS=['20250528','20250904','20251124','20260324','20260803','20260804']
POS_SET=set(POS)

def daily():
 d=relay_query('fund_daily',ts_code='588170.SH',start_date='20250101',end_date='20261021').sort_values('trade_date').reset_index(drop=True)
 for c in ['close','high','low','amount']: d[c]=pd.to_numeric(d[c],errors='coerce')
 d['ma20']=d.close.rolling(20).mean(); d['lo20']=d.low.rolling(20).min(); d['r20']=d.close/d.close.shift(20)-1
 return d

def mins(day):
 s=f'{day[:4]}-{day[4:6]}-{day[6:]}'
 d=relay_query('stk_mins',ts_code='588170.SH',freq='1min',start_date=f'{s} 09:30:00',end_date=f'{s} 15:00:00')
 if d is None or not len(d): return None
 d=d.sort_values('trade_time').reset_index(drop=True)
 for c in ['open','high','low','close','vol']: d[c]=pd.to_numeric(d[c],errors='coerce')
 d['dt']=pd.to_datetime(d.trade_time)
 return d

def feats(day,d):
 li=d.low.idxmin(); low=float(d.loc[li,'low']); t=str(d.loc[li,'trade_time']); c=float(d.close.iloc[-1]); tl=d.loc[li,'dt']
 p30=d[d.dt>=tl+pd.Timedelta(minutes=30)]
 p30=float(p30.close.iloc[0]) if len(p30) else None
 return {'date':day,'low':low,'low_time':t[-8:-3],'low_vol_ratio':round(float(d.loc[li,'vol']/max(d.vol.mean(),1)),2),
         'low_to_close':round(float(c/low-1)*100,2),
         'low_to_30m':round(float(p30/low-1)*100,2) if p30 else None,
         'close_pos':round(float((c-d.low.min())/(d.high.max()-d.low.min())),2) if d.high.max()>d.low.min() else None}

def main():
 d=daily(); lowzone=(d.close<=d.lo20*1.05)&(d.close<d.ma20)&(d.r20<0)
 # 排除正样本 ±2 个交易日
 pos_idx=[d[d.trade_date==x].index[0] for x in POS if (d.trade_date==x).any()]
 neg=[]
 for i in d[lowzone].index:
  if any(abs(i-j)<=2 for j in pos_idx): continue
  neg.append(d.trade_date.iloc[i])
 # 抽样，避免连续太多
 neg=neg[::4][:15]
 rows=[]
 for day in POS+neg:
  m=mins(day)
  if m is None: print('skip',day); continue
  f=feats(day,m); f['label']='pos' if day in POS_SET else 'neg'; rows.append(f)
 df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,'b_rebound_minute_candidates.csv'),index=False,encoding='utf-8-sig')
 print(df.to_string(index=False)); print('pos',len(df[df.label=='pos']),'neg',len(df[df.label=='neg']))

if __name__=='__main__': main()
