#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从 theme_membership 重建 theme_membership_core（主营 + 行业 + 配套段过滤）。"""
from __future__ import annotations
import argparse,os,sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'backend')): sys.path.insert(0,p)
from app.services.theme_universe_store import save_core_members,load_theme_config
from app.services.wolf_card.main_business import assess
from app.services.wolf_leader_rules import SUPPORT_INDUSTRY,SUPPORT_NAME
NOISE={'服饰','汽车服务','软件服务','IT设备','环境保护','文化传媒','商业百货','食品饮料','农林牧渔','银行','证券','保险'}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--apply',action='store_true');a=ap.parse_args()
 import psycopg2
 c=psycopg2.connect(os.environ['DATABASE_URL'],connect_timeout=5);cur=c.cursor()
 cur.execute("select max(as_of_date) from theme_membership");d=cur.fetchone()[0]
 cur.execute("select theme_id,symbol from theme_membership where as_of_date=%s",(d,));rows=cur.fetchall()
 cur.execute("select ts_code,name,industry,market_cap from stock_pool where ts_code=any(%s)",(list({r[1] for r in rows}),))
 meta={r[0]:(r[1],r[2],float(r[3] or 0)) for r in cur.fetchall()}
 cfg=load_theme_config(); names={t.get('id'):t.get('name') or t.get('id') for t in (cfg.get('themes') or [])+(cfg.get('satellite_themes') or [])}
 by={}
 for tid,sym in rows:
  name,ind,mv=meta.get(sym,('','',0))
  if ind in NOISE: continue
  if ind in SUPPORT_INDUSTRY or any(k in name for k in SUPPORT_NAME): continue
  r=assess(sym,names.get(tid) or tid)
  # 主营只作证据；核心/配套先按行业与名称分层，避免把尚未形成收入的未来核心误杀
  by.setdefault(tid,[]).append({'symbol':sym,'role':'core','ratio':r.get('ratio'),'reason':r.get('reason')})
 print('date',d,'themes',len(by),'core',sum(len(v) for v in by.values()))
 for tid,v in sorted(by.items()): print(tid,len(v))
 if a.apply:
  for tid,v in by.items(): save_core_members(tid,v,d.strftime('%Y%m%d'))
  print('saved')
 else: print('dry-run; use --apply')
 c.close()
if __name__=='__main__': main()
