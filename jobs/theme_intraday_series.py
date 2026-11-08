#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""每15分钟计算题材盘中 breadth/涨停/leader（一次抓全日分钟K）。"""
from __future__ import annotations
import argparse,json,os,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0,os.path.join(ROOT,'core'))
from tushare_relay import relay_query

def lim(c):
 c=str(c).upper()
 if c.startswith(('300','301','688','689')): return 20.0
 if c.startswith(('8','4','92')): return 30.0
 return 10.0

def fetch(c,day):
 try:
  x=relay_query('stk_mins',ts_code=c,freq='1min',start_date=f'{day[:4]}-{day[4:6]}-{day[6:]} 09:30:00',end_date=f'{day[:4]}-{day[4:6]}-{day[6:]} 15:00:00',limit=1000)
  if x is None or len(x)==0: return c,None
  return c,x.sort_values('trade_time')
 except Exception: return c,None

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);ap.add_argument('--concept',required=True);a=ap.parse_args();day=a.date
 snap=json.load(open(os.path.join(ROOT,'data/asof',f'{day}_snapshot.json'),encoding='utf-8'))['concepts']
 mem=[c for c,v in snap.items() if a.concept in v]
 daily=relay_query('daily',trade_date=day,limit=6000); pre={str(r.ts_code):float(r.pre_close) for _,r in daily.iterrows()}
 data={}
 with ThreadPoolExecutor(max_workers=10) as ex:
  for f in as_completed([ex.submit(fetch,c,day) for c in mem]):
   c,x=f.result()
   if x is not None: data[c]=x
 times=['0945','1000','1015','1030','1045','1100','1115','1130','1300','1315','1330','1345','1400','1415','1430','1445','1500']
 out=[]
 for hm in times:
  rows=[]
  for c,x in data.items():
   pc=pre.get(c)
   if not pc: continue
   y=x[x.trade_time.astype(str).str[-5:]<=f'{hm[:2]}:{hm[2:]}']
   if len(y)==0: continue
   p=float(y.iloc[-1]['close']); pct=(p/pc-1)*100
   rows.append({'symbol':c,'pct':round(pct,2),'limit':pct>=lim(c)-0.3})
  n=len(rows); up=sum(1 for r in rows if r['pct']>0); lu=sum(1 for r in rows if r['limit'])
  leader=max(rows,key=lambda r:r['pct']) if rows else None
  out.append({'time':hm,'n':n,'breadth':round(up/n,3) if n else 0,'limit_up':lu,
              'ret1':round(sum(r['pct'] for r in rows)/n,2) if n else 0,'leader':leader})
  print(hm,'n',n,'br',out[-1]['breadth'],'lu',lu,'ret',out[-1]['ret1'],'leader',leader)
 p=os.path.join(ROOT,'data/intraday',f'{day}_{a.concept}_15m.json');json.dump(out,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1);print('written',p)
if __name__=='__main__': main()
