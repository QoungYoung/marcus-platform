# -*- coding: utf-8 -*-
"""按语料对齐的 B反确认：二次缩量下探不破前低 + 补量 + 站上5日线。"""
from __future__ import annotations
import argparse, json, os, sys
import numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,ROOT)
from core.tushare_relay import relay_query

def load(code):
    d=relay_query('fund_daily',ts_code=code,start_date='20250101',end_date='20261021')
    d=d.sort_values('trade_date').reset_index(drop=True)
    for c in ['open','high','low','close','vol','amount']: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d

def detect(d):
    out=[]; last=-99
    for i in range(40,len(d)):
        if i-last<10: continue
        w=d.iloc[:i+1].copy()
        low1=w.low.iloc[-30:-10].min(); low1_i=w.low.iloc[-30:-10].idxmin()
        low2=w.low.iloc[-10:].min(); low2_i=w.low.iloc[-10:].idxmin()
        if not (low2 >= low1*0.995): continue  # 不破前低
        # 二次缩量：低2附近5日均额 < 低1附近5日均额
        a1=w.amount.iloc[max(0,low1_i-2):low1_i+3].mean(); a2=w.amount.iloc[max(0,low2_i-2):low2_i+3].mean()
        if not (pd.notna(a1) and pd.notna(a2) and a2 < a1*0.95): continue
        # 补量确认日
        ma5=w.close.rolling(5).mean().iloc[-1]
        a5=w.amount.tail(5).mean(); a20=w.amount.tail(20).mean()
        cond=(w.close.iloc[-1] > ma5 and w.close.iloc[-1] > w.close.iloc[-2]
              and pd.notna(a20) and a5 >= a20*0.8
              and w.amount.iloc[-1] >= a20*0.9)
        if cond:
            out.append({'date':w.trade_date.iloc[-1],'low1_date':w.trade_date.iloc[low1_i],
                        'low1':float(low1),'low2_date':w.trade_date.iloc[low2_i],'low2':float(low2),
                        'close':float(w.close.iloc[-1]),'ma5':float(ma5)})
            last=i
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--code',default='588170.SH'); a=ap.parse_args()
    d=load(a.code); ev=detect(d)
    print(a.code,len(ev)); print(pd.DataFrame(ev).to_string(index=False) if ev else 'none')
if __name__=='__main__': main()
