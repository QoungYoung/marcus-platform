#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""asof_data.py — 只读 as-of 数据接口；默认硬锁 20261028，拒绝未来数据。"""
from __future__ import annotations
import argparse, json, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'core'),os.path.join(ROOT,'backend')):
    if p not in sys.path: sys.path.insert(0,p)
CUTOFF=os.getenv('ASOF_CUTOFF','20261028')

def guard(d):
    if str(d)>CUTOFF:
        raise SystemExit(f'refused: {d} > as_of {CUTOFF}')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default=CUTOFF); ap.add_argument('--out',default='')
    a=ap.parse_args(); guard(a.date)
    from tushare_relay import relay_query
    import psycopg2
    daily=relay_query('daily',trade_date=a.date,limit=6000)
    limit=relay_query('limit_list_d',trade_date=a.date,limit=6000)
    c=psycopg2.connect(os.environ['DATABASE_URL']);cur=c.cursor()
    cur.execute('select ts_code,concept_name from stock_concept_map'); cm={}
    for code,name in cur.fetchall(): cm.setdefault(code,[]).append(name)
    c.close()
    snap={'as_of':a.date,'cutoff':CUTOFF,
          'daily':[{k:r.get(k) for k in ('ts_code','close','pct_chg','amount','high','low','open','pre_close')} for _,r in daily.iterrows()],
          'limit':[{k:r.get(k) for k in ('ts_code','limit','open_times','first_time','last_time','fd_amount','limit_times','up_stat')} for _,r in limit.iterrows()],
          'concepts':cm}
    out=a.out or os.path.join(ROOT,'data','asof',f'{a.date}_snapshot.json')
    os.makedirs(os.path.dirname(out),exist_ok=True)
    json.dump(snap,open(out,'w',encoding='utf-8'),ensure_ascii=False)
    print(json.dumps({'ok':True,'as_of':a.date,'daily':len(snap['daily']),'limit':len(snap['limit']),'stocks_with_concepts':len(cm),'out':out},ensure_ascii=False))
if __name__=='__main__': main()
