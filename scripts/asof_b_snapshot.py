#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""asof_b_snapshot.py — 给 B agent 的 10/28 盲测数据包（硬锁日期）。"""
from __future__ import annotations
import argparse, json, os, pickle, sys
import numpy as np, pandas as pd
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'core'),os.path.join(ROOT,'backend')):
    if p not in sys.path: sys.path.insert(0,p)
CUTOFF='20261028'

def guard(d):
    if str(d)>CUTOFF: raise SystemExit('refused future date')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default=CUTOFF); ap.add_argument('--top',type=int,default=60)
    a=ap.parse_args(); guard(a.date)
    snap=json.load(open(f'data/asof/{a.date}_snapshot.json',encoding='utf-8'))
    stats={x['concept']:x for x in json.load(open(f'data/asof/{a.date}_concept_stats.json',encoding='utf-8'))['concepts']}
    ov=json.load(open(f'data/asof/{a.date}_source_overlap.json',encoding='utf-8'))
    cmap=snap['concepts']; daily={str(x['ts_code']):x for x in snap['daily'] if x.get('ts_code')}
    lim={str(x['ts_code']):x for x in snap['limit'] if x.get('ts_code')}
    bars=pickle.load(open(f'data/bars_cache/{a.date}.pkl','rb'))
    from tushare_relay import relay_query
    idx={}
    for code in ('000001.SH','399006.SZ'):
        x=relay_query('index_daily',ts_code=code,start_date='20260401',end_date=a.date,limit=300)
        if x is not None and len(x):
            x=x.sort_values('trade_date'); idx[code]=[{'d':str(r.trade_date),'c':round(float(r.close),2),'pct':round(float(r.pct_chg),2)} for _,r in x.iterrows()]
    bench={x['d']:x['c'] for x in idx.get('000001.SH',[])}
    def members(name): return {c for c,v in cmap.items() if name in v}
    src=set().union(*[members(x) for x in ov['source_concepts']])
    # bars metrics helper
    def metrics(codes):
        rows=[]
        for c in codes:
            b=bars.get(c)
            if b is None or len(b)<25: continue
            b=b[b.trade_date.astype(str)<=a.date].sort_values('trade_date')
            if len(b)<25: continue
            close=b.close.astype(float); pct=b.pct_chg.astype(float)
            ret5=close.iloc[-1]/close.iloc[-6]-1
            ret20=close.iloc[-1]/close.iloc[-21]-1
            # popularity: top3 pct days in last120
            top3=(pct.tail(120).rank(ascending=False)<=3).sum()
            rows.append({'symbol':c,'ret5':round(float(ret5),4),'ret20':round(float(ret20),4),
                         'popular_top3_120':int(top3),'pct_last':round(float(pct.iloc[-1]),2)})
        return rows
    cands=[]
    for r in ov.get('candidates',[])[:a.top]:
        name=r['concept']; mem=members(name)
        if not mem: continue
        ms=metrics(sorted(mem))
        # theme relative strength vs benchmark
        d5={x['d']:x['c'] for x in idx.get('000001.SH',[])}
        benchmark_ret20=(float(ov.get('_bench') or 0) if False else None)
        # convenience: compute from index series if available
        idx20=idx.get('000001.SH',[])
        b20=(idx20[-1]['c']/idx20[-21]['c']-1) if len(idx20)>21 else None
        b5=(idx20[-1]['c']/idx20[-6]['c']-1) if len(idx20)>6 else None
        avg5=np.mean([x['ret5'] for x in ms]) if ms else None
        avg20=np.mean([x['ret20'] for x in ms]) if ms else None
        cands.append({**r,'members_n':len(mem),'members_sample':sorted(mem)[:30],
                      'member_metrics':ms[:20],
                      'avg_ret5':round(float(avg5),4) if avg5 is not None else None,
                      'avg_ret20':round(float(avg20),4) if avg20 is not None else None,
                      'bench_ret5':round(float(b5),4) if b5 is not None else None,
                      'bench_ret20':round(float(b20),4) if b20 is not None else None,
                      'excess5':round(float(avg5-b5),4) if avg5 is not None and b5 is not None else None,
                      'excess20':round(float(avg20-b20),4) if avg20 is not None and b20 is not None else None,
                      'popular_top3_sum':int(sum(x['popular_top3_120'] for x in ms)) if ms else 0})
    facts={}
    for p in (f'data/mainline/{a.date}/theme_sleeve_facts.json', f'data/mainline/20261027/theme_sleeve_facts.json', f'data/mainline/{a.date}/theme.json', f'data/mainline/20261027/theme.json'):
        if os.path.exists(p): facts[os.path.basename(os.path.dirname(p))+'/'+os.path.basename(p)]=json.load(open(p,encoding='utf-8'))
    out={'as_of':a.date,'cutoff':CUTOFF,'source_concepts':ov.get('source_concepts'),'source_members_n':len(src),
         'index_kline':idx,'candidates':cands,
         'fact_files':facts,
         'data_notes':['daily/limit/concepts/overlap <= cutoff','index_kline <= cutoff','concept membership current allowed','member_metrics from bars_cache <= cutoff']}
    p=f'data/asof/{a.date}_b_snapshot.json'; os.makedirs(os.path.dirname(p),exist_ok=True)
    json.dump(out,open(p,'w',encoding='utf-8'),ensure_ascii=False)
    print(json.dumps({'ok':True,'candidates':len(cands),'index':list(idx),'out':p},ensure_ascii=False))
if __name__=='__main__': main()
