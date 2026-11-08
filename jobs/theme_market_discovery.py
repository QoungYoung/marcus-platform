#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_market_discovery.py — 全市场概念候选发现（不交易）。"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime
import yaml
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'core'),os.path.join(ROOT,'backend')):
    if p not in sys.path: sys.path.insert(0,p)
from tushare_relay import relay_query

GENERIC=("融资融券","沪股通","深股通","昨日","最近","机构","基金","预盈","预亏","ST","次新","破净","高送转","举牌","回购","转债","同花顺","标的","指数","成份","大盘","中盘","小盘","AH","B股","GDR","MSCI","富时","标普")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); a=ap.parse_args()
    import psycopg2
    c=psycopg2.connect(os.environ['DATABASE_URL']); cur=c.cursor()
    cur.execute("select concept_name,array_agg(ts_code) from stock_concept_map group by concept_name")
    rows=cur.fetchall(); c.close()
    df=relay_query('daily',trade_date=a.date,limit=6000)
    px={str(r.get('ts_code')):float(r.get('pct_chg') or 0) for _,r in df.iterrows()} if df is not None else {}
    lu=relay_query('limit_list_d',trade_date=a.date,limit=6000)
    ups={str(r.get('ts_code')) for _,r in lu.iterrows() if str(r.get('limit') or '').upper()=='U'} if lu is not None else set()
    try:
        sys.path.insert(0, os.path.join(ROOT, "backend"))
        from app.services.theme_universe_store import load_theme_config
        cfg=load_theme_config() or {}
    except Exception:
        cfg={}
    if not cfg:
        cfg=yaml.safe_load(open(os.path.join(ROOT,'config','mainline','generated','universe_theme.yaml'),encoding='utf-8'))
    known=set()
    for t in (cfg.get('themes') or [])+(cfg.get('satellite_themes') or []): known.add(str(t.get('name') or '')); known.add(str(t.get('id') or ''))
    # 财务/风格属性标签过滤（配置驱动，避免写死每个概念名）
    try:
        _fc = yaml.safe_load(open(os.path.join(ROOT, "config", "mainline", "theme_discovery_filter.yaml"), encoding="utf-8")) or {}
        _attrs = [str(x) for x in (_fc.get("attribute_patterns") or [])]
    except Exception:
        _attrs = []
    out=[]
    for name,codes in rows:
        if any(g in str(name) for g in GENERIC): continue
        if any(g in str(name) for g in _attrs): continue
        codes=[str(x) for x in codes]
        if len(codes)<5 or len(codes)>300: continue
        vals=[px[x] for x in codes if x in px]
        if len(vals)<5: continue
        avg=sum(vals)/len(vals)
        breadth=sum(1 for v in vals if v>0)/len(vals)
        u=sum(1 for x in codes if x in ups)
        hits=sum([avg>0.02,breadth>=0.6,u>=2])
        covered=any((k and (k in str(name) or str(name) in k)) for k in known)
        if hits>=2:
            top=max(((px.get(x,0),x) for x in codes),default=(0,''))
            out.append({'concept':name,'n':len(codes),'avg_ret':round(avg,4),'breadth':round(breadth,3),
                        'limit_up':u,'hits':hits,'covered':covered,'leader':top[1],'leader_pct':round(top[0],4)})
    out.sort(key=lambda x:(-x['hits'],-x['limit_up'],-x['breadth'],-x['avg_ret']))
    p=os.path.join(ROOT,'data','theme_market_discovery.json')
    payload={'date':a.date,'total':len(out),'candidates':out[:50]}
    json.dump(payload,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    try:
        from theme_candidate_store import save
        save(a.date,'discovery',payload)
    except Exception as e: print('[pg] discovery save fail',str(e)[:100])
    print('date',a.date,'candidates',len(out))
    for x in out[:30]: print(x)
    print('written',p)
    return 0
if __name__=='__main__': raise SystemExit(main())
