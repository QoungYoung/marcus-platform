# -*- coding: utf-8 -*-
"""B反文本+价格事件库（人工从语料抽取日期/阶段，价格特征自动算）。"""
from __future__ import annotations
import json, os, sys
import numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0,ROOT)
from core.tushare_relay import relay_query
OUT=os.path.join(ROOT,'data/core_pit'); os.makedirs(OUT,exist_ok=True)

# date, asset, phase, source, note
EVENTS=[
 ('20250804','588170.SH','mid','wolf-daily-log-xls2025','B反未完，先拿住50%不追高'),
 ('20251203','588170.SH','end','wolf-daily-log-xls2025','这次B反涨幅2天就吃到，直接转防守'),
 ('20260416','588170.SH','mid','wolf-buy-parameter-ledger','做B反一致性，防御性高切低'),
 ('20260507','588170.SH','high','wolf-daily-log-xls2026','15分钟级别找B反高点与二卖位'),
 ('20260521','588170.SH','end','wolf-daily-log-xls2026','B反结束当天直接接C杀'),
 ('20260803','588170.SH','low','nga:1271/1282','科创半导体ETF B反高点1.15，后面C杀；低点做T'),
 ('20260804','588170.SH','mid','wolf-daily-log-nga','光和半导体只是B反预期，不做T死拿回不了本'),
 ('20260805','588170.SH','mid','wolf-daily-log-nga','B反是一次性的，要信早信'),
 ('20260824','588170.SH','tail','wolf-daily-log-nga','仍是科技ABC的B反，自由落体乒乓球'),
 ('20250410','588170.SH','wave_label','wave_state_history','wave B反标签'),
 ('20251230','588170.SH','wave_label','wave_state_history','wave B反标签'),
 ('20260225','588170.SH','wave_label','wave_state_history','wave B反标签'),
 ('20260615','588170.SH','wave_label','wave_state_history','wave B反标签'),
 ('20260827','588170.SH','wave_label','wave_state_history','wave B反标签'),
]

def load_asset(code):
    if code.endswith('.SH') and code.startswith(('58','51')):
        d=relay_query('fund_daily',ts_code=code,start_date='20250101',end_date='20261021')
    else:
        d=relay_query('index_daily',ts_code=code,start_date='20250101',end_date='20261021')
    d=d.sort_values('trade_date').reset_index(drop=True)
    for c in ['open','high','low','close','amount','pct_chg']: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d

def main():
    rows=[]
    cache={}
    for date,asset,phase,source,note in EVENTS:
        if asset not in cache: cache[asset]=load_asset(asset)
        d=cache[asset]; x=d[d.trade_date==date]
        if not len(x): print('missing',date); continue
        i=x.index[0]; r=x.iloc[0]
        if i<20 or i+21>=len(d): continue
        ma5=d.close.rolling(5).mean().iloc[i]; ma10=d.close.rolling(10).mean().iloc[i]; ma20=d.close.rolling(20).mean().iloc[i]
        hi20=d.high.rolling(20).max().iloc[i]; lo20=d.low.rolling(20).min().iloc[i]; hi60=d.high.rolling(60).max().iloc[i]
        a20=d.amount.rolling(20).mean().iloc[i]
        pos=(r.close-r.low)/(r.high-r.low) if r.high>r.low else np.nan
        f=[]
        for h in (5,10,20):
            j=i+h
            f.append(float(d.close.iloc[j]/d.close.iloc[i]-1) if j<len(d) else np.nan)
        rows.append({'date':date,'asset':asset,'phase':phase,'source':source,'note':note,
                     'close':round(float(r.close),4),'ret1':round(float(r.pct_chg),2),
                     'r5':round(float(r.close/d.close.iloc[i-5]-1)*100,2),
                     'r20':round(float(r.close/d.close.iloc[i-20]-1)*100,2),
                     'dd20':round(float(r.close/hi20-1)*100,2),
                     'dd60':round(float(r.close/hi60-1)*100,2),
                     'dist_lo20':round(float(r.close/lo20-1)*100,2),
                     'amt_ratio':round(float(r.amount/a20),2) if pd.notna(a20) and a20 else None,
                     'close_pos':round(float(pos),2) if pd.notna(pos) else None,
                     'ma5':round(float(r.close/ma5-1),3),'ma10':round(float(r.close/ma10-1),3),'ma20':round(float(r.close/ma20-1),3),
                     'f5':round(f[0]*100,2) if pd.notna(f[0]) else None,
                     'f10':round(f[1]*100,2) if pd.notna(f[1]) else None,
                     'f20':round(f[2]*100,2) if pd.notna(f[2]) else None})
    df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,'b_rebound_events.csv'),index=False,encoding='utf-8-sig')
    json.dump({'n':len(df),'phases':df.phase.value_counts().to_dict()},open(os.path.join(OUT,'b_rebound_events_summary.json'),'w'),ensure_ascii=False,indent=1)
    print(df.to_string(index=False))
    print(json.dumps({'n':len(df),'phases':df.phase.value_counts().to_dict()},ensure_ascii=False))

if __name__=='__main__': main()
