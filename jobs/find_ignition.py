# -*- coding: utf-8 -*-
"""启动候选：首次进入主题强势 + 放量 + 站上/突破 + 未过度延伸。"""
from __future__ import annotations
import argparse, json, os
import numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT=os.path.join(ROOT,"data/core_pit"); os.makedirs(OUT,exist_ok=True)
NAMES={'603986.SH':'兆易创新','300223.SZ':'北京君正','688008.SH':'澜起科技','301308.SZ':'江波龙','688525.SH':'佰维存储','001309.SZ':'德明利','300475.SZ':'香农芯创','000021.SZ':'深科技','600667.SH':'太极实业','688123.SH':'聚辰股份','688110.SH':'东芯股份','688766.SH':'普冉股份'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--code",default="001309.SZ"); a=ap.parse_args()
    b=pd.read_parquet(os.path.join(ROOT,"data/bars_api.parquet"),columns=['ts_code','trade_date','close','high','amount'])
    b=b[b.ts_code.isin(NAMES)].sort_values(['ts_code','trade_date'])
    p=b.pivot_table(index='trade_date',columns='ts_code',values='close',aggfunc='last').sort_index().loc['20250101':]
    h=b.pivot_table(index='trade_date',columns='ts_code',values='high',aggfunc='last').sort_index().loc['20250101':]
    amt=b.pivot_table(index='trade_date',columns='ts_code',values='amount',aggfunc='last').sort_index().loc['20250101':]
    r5=p.pct_change(5); r10=p.pct_change(10); ma20=p.rolling(20).mean(); hi20=h.rolling(20).max()
    rank5=r5.rank(axis=1,ascending=False,method='min')
    amtr=amt.rolling(5).mean()/amt.rolling(20).mean()
    idx={d:i for i,d in enumerate(p.index)}; rows=[]
    for d in p.index:
        c=a.code; i=idx[d]
        if c not in p.columns or i<21 or i+21>=len(p.index): continue
        prev=rank5.shift(1).loc[d,c]; now=rank5.loc[d,c]
        first = pd.notna(now) and now<=3 and (pd.isna(prev) or prev>5)
        volok = pd.notna(amtr.loc[d,c]) and amtr.loc[d,c]>=1.2
        priceok = pd.notna(hi20.shift(1).loc[d,c]) and p.loc[d,c]>=hi20.shift(1).loc[d,c]*0.98
        notext = pd.notna(ma20.loc[d,c]) and p.loc[d,c] <= ma20.loc[d,c]*1.15
        if first and volok and priceok and notext:
            rows.append({"date":d,"code":c,"name":NAMES[c],"r5":round(float(r5.loc[d,c])*100,2),
                         "r10":round(float(r10.loc[d,c])*100,2),"r20":round(float(p[c].iloc[i]/p[c].iloc[i-20]-1)*100,2),
                         "amt_ratio":round(float(amtr.loc[d,c]),2),
                         "f5":round(float(p[c].iloc[i+6]/p[c].iloc[i+1]-1)*100,2),
                         "f10":round(float(p[c].iloc[i+11]/p[c].iloc[i+1]-1)*100,2),
                         "f20":round(float(p[c].iloc[i+21]/p[c].iloc[i+1]-1)*100,2)})
    df=pd.DataFrame(rows); df.to_csv(os.path.join(OUT,"ignition_candidates.csv"),index=False,encoding="utf-8-sig")
    print(df.to_string(index=False) if len(df) else "no candidates")
    if len(df): print("\nmean",df[['f5','f10','f20']].mean().round(2).to_dict())
if __name__=="__main__": main()
