#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_intraday_snapshot.py — 盘中/回放题材快照（分钟K）。

用法：
  python jobs/theme_intraday_snapshot.py --date 20261028 --time 1330 --concepts 商业航天
输出：data/intraday/<date>_<time>_<concept>.json
"""
from __future__ import annotations
import argparse, json, os, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT,'core'))
from tushare_relay import relay_query

def limit_pct(code: str) -> float:
    c=str(code).upper()
    if c.startswith(('300','301','688','689')): return 0.20
    if c.startswith(('8','4','92')): return 0.30
    return 0.10

def one(code, day, hm):
    try:
        s=f"{day[:4]}-{day[4:6]}-{day[6:]} 09:30:00"; e=f"{day[:4]}-{day[4:6]}-{day[6:]} {hm[:2]}:{hm[2:]}:59"
        x=relay_query('stk_mins',ts_code=code,freq='1min',start_date=s,end_date=e,limit=1000)
        if x is None or len(x)==0: return code,None
        x=x.sort_values('trade_time'); last=x.iloc[-1]
        return code,float(last['close'])
    except Exception:
        return code,None

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); ap.add_argument('--time',default='1330'); ap.add_argument('--concepts',required=True)
    a=ap.parse_args(); day=a.date; hm=a.time
    snap=json.load(open(os.path.join(ROOT,'data/asof',f'{day}_snapshot.json'),encoding='utf-8'))['concepts']
    daily=relay_query('daily',trade_date=day,limit=6000); pre={str(r.ts_code):float(r.pre_close) for _,r in daily.iterrows()}
    out={}
    for name in [x.strip() for x in a.concepts.split(',') if x.strip()]:
        mem=[c for c,v in snap.items() if name in v]
        vals={}
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs={ex.submit(one,c,day,hm):c for c in mem}
            for f in as_completed(futs):
                c,v=f.result()
                if v is not None: vals[c]=v
        rows=[]
        for c,p in vals.items():
            pc=pre.get(c)
            if not pc: continue
            pct=(p/pc-1)*100
            rows.append({'symbol':c,'price':p,'pre_close':pc,'pct':round(pct,2),'limit':pct>=limit_pct(c)*100-0.3})
        n=len(rows); up=sum(1 for r in rows if r['pct']>0); lu=sum(1 for r in rows if r['limit'])
        avg=sum(r['pct'] for r in rows)/n if n else 0
        leader=max(rows,key=lambda r:r['pct']) if rows else None
        out[name]={'date':day,'time':hm,'n':n,'up':up,'breadth':round(up/n,3) if n else 0,
                   'limit_up':lu,'ret1':round(avg,2),'leader':leader,
                   'top':sorted(rows,key=lambda r:-r['pct'])[:10]}
        print(name,'n',n,'breadth',out[name]['breadth'],'limit',lu,'ret1',out[name]['ret1'],'leader',leader)
    os.makedirs(os.path.join(ROOT,'data/intraday'),exist_ok=True)
    p=os.path.join(ROOT,'data/intraday',f'{day}_{hm}_intraday.json')
    old={}
    if os.path.exists(p):
        try: old=json.load(open(p,encoding='utf-8'))
        except Exception: old={}
    old.update(out)
    json.dump(old,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1); print('written',p)
if __name__=='__main__': main()
