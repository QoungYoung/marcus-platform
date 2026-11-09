#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_ab_exit.py — C agent exit：按当前模拟盘持仓市价卖出。"""
from __future__ import annotations
import argparse,json,os,sys,urllib.request
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API=(os.getenv('MARCUS_API_URL') or 'http://127.0.0.1:8000').rstrip('/')
def req(path,method='GET',payload=None):
 data=json.dumps(payload).encode() if payload is not None else None
 r=urllib.request.Request(API+path,data=data,headers={'Content-Type':'application/json'},method=method)
 with urllib.request.urlopen(r,timeout=60) as x:return json.loads(x.read().decode())
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--symbol',required=True);ap.add_argument('--date',default='');a=ap.parse_args()
 sym=a.symbol
 pos=req('/api/v1/portfolio/positions?account=stock')
 p=next((x for x in pos if str(x.get('symbol')).replace('.SH','').replace('.SZ','').endswith(sym.split('.')[0])),None)
 if not p: print('no position',sym);return 0
 vol=int(p.get('volume') or 0); px=float(p.get('current_price') or p.get('avg_price') or 0)
 if vol<=0 or px<=0: print('bad position',p);return 1
 r=req('/api/v1/trades','POST',{'symbol':p.get('symbol'),'side':'sell','price':px,'volume':vol,'reason':'theme_sleeve:C_exit','account':'stock'})
 print(json.dumps(r,ensure_ascii=False));return 0
if __name__=='__main__': raise SystemExit(main())
