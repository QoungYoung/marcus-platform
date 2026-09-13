# -*- coding: utf-8 -*-
import pandas as pd, numpy as np, json
close=pd.read_csv('data/指数数据/index_daily/000001.SH.csv', parse_dates=['trade_date']).set_index('trade_date')['close']
close=close[close.index<=pd.Timestamp('2026-06-26')]
def fwd(d):
    base=close[close.index<=d]; c=float(base.iloc[-1])
    fut=close[close.index>d]
    if len(fut)<1: return None
    peak=fut.max(); trough=fut.min()
    r20=(float(fut.iloc[19])/c-1)*100 if len(fut)>=20 else None
    r60=(float(fut.iloc[59])/c-1)*100 if len(fut)>=60 else None
    r120=(float(fut.iloc[119])/c-1)*100 if len(fut)>=120 else None
    return dict(close=round(c,1),max_gain_pct=round((peak/c-1)*100,1),max_dd_pct=round((trough/c-1)*100,1),peak_date=str(fut.idxmax().date()),peak_val=round(float(peak),1),trough_date=str(fut.idxmin().date()),trough_val=round(float(trough),1),r20=r20,r60=r60,r120=r120)
for d in ['2025-03-27','2025-04-09','2025-10-19']:
    print("FWD", d, fwd(d))
piv=json.load(open('data/wave_pivots.json'))['pivots']
for d in ['2025-03-27','2025-04-09','2025-10-19']:
    t=pd.Timestamp(d); nxt=[p for p in piv if pd.Timestamp(p['date'])>t]
    print("NEXTPIVOT", d, [(p['date'],p['type'],p['value']) for p in nxt[:3]])
