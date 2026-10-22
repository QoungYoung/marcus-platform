# -*- coding: utf-8 -*-
"""B反低点分钟特征：放量恐慌 / 缩量止跌两类。"""
from __future__ import annotations
import os,sys,json
import numpy as np,pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,ROOT)
from core.tushare_relay import relay_query
OUT=os.path.join(ROOT,'data/core_pit')
LOWS=[
 ('20260803','panic','B反预期，14:57见低'),
 ('20260804','panic_reversal','9:34见低后反转'),
 ('20250904','panic','放量下杀'),
 ('20250528','shrink','缩量止跌'),
 ('20251124','shrink','缩量止跌'),
 ('20260324','shrink','缩量止跌'),
]

def load(day):
 s=f'{day[:4]}-{day[4:6]}-{day[6:]}'
 d=relay_query('stk_mins',ts_code='588170.SH',freq='1min',start_date=f'{s} 09:30:00',end_date=f'{s} 15:00:00')
 if d is None or not len(d): return None
 d=d.sort_values('trade_time').reset_index(drop=True)
 for c in ['open','high','low','close','vol','amount']: d[c]=pd.to_numeric(d[c],errors='coerce')
 return d

def main():
 rows=[]
 for day,kind,note in LOWS:
  d=load(day)
  if d is None: print('skip',day); continue
  li=d.low.idxmin(); low=d.loc[li,'low']; t=str(d.loc[li,'trade_time']); c=d.close.iloc[-1]
  # 30/60 min after low
  d['dt']=pd.to_datetime(d.trade_time)
  tlow=d.loc[li,'dt']
  def px_after(m):
   z=d[d.dt>=tlow+pd.Timedelta(minutes=m)]
   return float(z.close.iloc[0]) if len(z) else None
  p30=px_after(30); p60=px_after(60)
  # second low after 30m
  z=d[d.dt>=tlow+pd.Timedelta(minutes=30)]
  second=float(z.low.min()) if len(z) else None
  rows.append({'date':day,'kind':kind,'note':note,'day_close':round(float(c),4),
               'low':round(float(low),4),'low_time':t[-8:-3],'low_vol_ratio':round(float(d.loc[li,'vol']/max(d.vol.mean(),1)),2),
               'low_to_close':round(float(c/low-1)*100,2),
               'low_to_30m':round(float(p30/low-1)*100,2) if p30 else None,
               'low_to_60m':round(float(p60/low-1)*100,2) if p60 else None,
               'second_low_after30':round(float(second),4) if second else None,
               'second_lower':bool(second is not None and second < low),
               'day_high':round(float(d.high.max()),4),
               'close_pos_day':round(float((c-d.low.min())/(d.high.max()-d.low.min())),2) if d.high.max()>d.low.min() else None})
  print(day,kind,'low',low,t[-8:-3],'volR',rows[-1]['low_vol_ratio'],'toClose',rows[-1]['low_to_close'],'30m',rows[-1]['low_to_30m'],'secondLower',rows[-1]['second_lower'])
 df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,'b_rebound_minute_lows.csv'),index=False,encoding='utf-8-sig')
 json.dump({'n':len(df),'by_kind':df.kind.value_counts().to_dict()},open(os.path.join(OUT,'b_rebound_minute_lows.json'),'w'),ensure_ascii=False,indent=1)
 print(df.to_string(index=False))

if __name__=='__main__': main()
