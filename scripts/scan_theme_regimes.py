#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""scan_theme_regimes.py — 2026-07~09 题材月度：板块行情 vs 龙头行情（研究）."""
from __future__ import annotations
import json, os, sys, statistics
from collections import defaultdict
import yaml
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'core'),os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend')):
    if p not in sys.path: sys.path.insert(0,p)
from tushare_relay import relay_query
from mainline_common import trade_dates
from stock_agent_facts import members_for

def y(p):
    with open(p,encoding='utf-8') as f: return yaml.safe_load(f) or {}
def main():
    start,end='20260701','20260930'
    dates=[str(x) for x in trade_dates(end) if start<=str(x)<=end]
    print('dates',len(dates),dates[0],dates[-1],flush=True)
    px={}
    for d in dates:
        try:
            df=relay_query('daily',trade_date=d,limit=6000)
            px[d]={str(r.get('ts_code')):float(r.get('pct_chg') or 0) for _,r in (df.iterrows() if df is not None else [])}
        except Exception as e:
            print('daily fail',d,e,flush=True); px[d]={}
    cfg=y(os.path.join(ROOT,'config','mainline','generated','universe_theme.yaml'))
    themes=[]
    for t in (cfg.get('themes') or []):
        srcs=list(t.get('sources') or [])+list(((t.get('resolution') or {}).get('selected')) or [])
        t2=dict(t); t2['sources']=srcs
        try: mem=sorted(set(members_for(t2,dates[-1])))
        except Exception: mem=[]
        themes.append((t,mem))
    # monthly: equal avg daily ret, avg breadth, leader avg
    for month in ('202607','202608','202609'):
        ds=[d for d in dates if d.startswith(month)]
        rows=[]
        for t,mem in themes:
            if not mem: continue
            day_rets=[]; brs=[]; ld=[]
            lc=(t.get('leader') or {}).get('code')
            for d in ds:
                vals=[px.get(d,{}).get(c) for c in mem if c in px.get(d,{})]
                vals=[v for v in vals if v is not None]
                if not vals: continue
                day_rets.append(sum(vals)/len(vals)/100)
                brs.append(sum(1 for v in vals if v>0)/len(vals))
                if lc in px.get(d,{}): ld.append(px[d][lc]/100)
            if not day_rets: continue
            cum=1.0
            for r in day_rets: cum*=1+r
            lead=1.0
            for r in ld: lead*=1+r
            rows.append({'id':t['id'],'name':t.get('name'),'ret':round(cum-1,4),'breadth':round(sum(brs)/len(brs),3),'leader':round(lead-1,4),'n':len(mem)})
        rows.sort(key=lambda x:-x['ret'])
        print('\n==',month,'top10')
        for r in rows[:10]: print(r)
        # classify
        print('sector:',[r['name'] for r in rows if r['ret']>0.08 and r['breadth']>0.55][:8])
        print('leader-only:',[r['name'] for r in rows if r['leader']>0.12 and (r['ret']<=0.03 or r['breadth']<0.5)][:8])
    return 0
if __name__=='__main__': raise SystemExit(main())
