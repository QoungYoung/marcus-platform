#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_intraday_live.py — 用 promax/datahub relay 的 rt_k 生成盘中 A 数据。"""
from __future__ import annotations
import argparse,json,os,sys,time
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)));sys.path.insert(0,os.path.join(ROOT,'core'))
from tushare_relay import relay_query

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--date',required=True);a=ap.parse_args();day=a.date
 import psycopg2
 c=psycopg2.connect(os.environ['DATABASE_URL']);cur=c.cursor()
 src_ids=['robot_actuator','humanoid_robot']
 cur.execute("select theme_id,symbol from theme_membership where theme_id=any(%s) and as_of_date=(select max(as_of_date) from theme_membership where theme_id=any(%s))",(src_ids,src_ids))
 by={}
 for tid,sym in cur.fetchall(): by.setdefault(tid,set()).add(sym)
 c.close()
 out={}
 for tid,mem in by.items():
  rows=[]
  for sym in sorted(mem):
   try:
    x=relay_query('rt_k',ts_code=sym,limit=1)
    if x is None or len(x)==0: continue
    r=x.iloc[0]; pc=float(r['pre_close'] or 0); cl=float(r['close'] or 0)
    if pc<=0: continue
    pct=(cl/pc-1)*100
    rows.append({'symbol':sym,'pct':round(pct,2),'limit':pct>=9.7})
   except Exception: continue
  n=len(rows); up=sum(1 for r in rows if r['pct']>0); lu=sum(1 for r in rows if r['limit'])
  out[tid]={'n':n,'breadth':round(up/n,3) if n else 0,'limit_up':lu,'ret1':round(sum(r['pct'] for r in rows)/n,2) if n else 0,'members':rows}
  print(tid,n,out[tid]['breadth'],lu,out[tid]['ret1'])
 os.makedirs(os.path.join(ROOT,'data/asof'),exist_ok=True)
 p=os.path.join(ROOT,'data/asof',f'{day}_a_intraday.json');json.dump({'date':day,'source':'机器人链','source_intraday':out,'candidate_meta':{}},open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1);print('written',p)
if __name__=='__main__': main()
