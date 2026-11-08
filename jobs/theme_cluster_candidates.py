#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_cluster_candidates.py — 全市场候选按成员重叠聚类，并按成员映射现有题材。"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
import yaml
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'core'),os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend')):
    if p not in sys.path: sys.path.insert(0,p)
from stock_agent_facts import members_for


def jac(a,b):
    if not a or not b: return 0.0
    return len(a & b)/len(a | b)

def overlap(a,b):
    if not a or not b: return 0.0
    return len(a & b)/min(len(a),len(b))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); ap.add_argument('--threshold',type=float,default=0.45)
    a=ap.parse_args()
    disc=json.load(open(os.path.join(ROOT,'data','theme_market_discovery.json'),encoding='utf-8'))
    cands=disc.get('candidates') or []
    names=[x['concept'] for x in cands]
    import psycopg2
    c=psycopg2.connect(os.environ['DATABASE_URL']); cur=c.cursor()
    cur.execute("select concept_name,array_agg(ts_code) from stock_concept_map where concept_name = ANY(%s) group by concept_name",(names,))
    cmap={str(r[0]):set(str(x) for x in r[1]) for r in cur.fetchall()}; c.close()
    # graph connected components by Jaccard
    idx={n:i for i,n in enumerate(names)}
    parent=list(range(len(names)))
    def find(x):
        while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
        return x
    def union(x,y):
        rx,ry=find(x),find(y)
        if rx!=ry: parent[ry]=rx
    for i,n1 in enumerate(names):
        for j in range(i+1,len(names)):
            if overlap(cmap.get(n1,set()),cmap.get(names[j],set()))>=a.threshold: union(i,j)
    groups=defaultdict(list)
    for n in names: groups[find(idx[n])].append(n)
    # existing theme members：按日期缓存，阈值扫描不重复取数
    cache_path=os.path.join(ROOT,'data',f'theme_members_cache_{a.date}.json')
    try:
        _cache=json.load(open(cache_path,encoding='utf-8'))
    except Exception:
        _cache={}
    if not _cache:
        try:
            sys.path.insert(0,os.path.join(ROOT,'backend'))
            from app.services.theme_universe_store import load_theme_config
            cfg=load_theme_config() or {}
        except Exception:
            cfg={}
        if not cfg:
            cfg=yaml.safe_load(open(os.path.join(ROOT,'config','mainline','generated','universe_theme.yaml'),encoding='utf-8'))
        for t in (cfg.get('themes') or [])+(cfg.get('satellite_themes') or []):
            srcs=list(t.get('sources') or [])+list(((t.get('resolution') or {}).get('selected')) or [])
            t2=dict(t); t2['sources']=srcs
            try: mem=set(members_for(t2,a.date))
            except Exception: mem=set()
            if mem: _cache[t.get('id')]={'name':t.get('name'),'members':sorted(mem)}
        json.dump(_cache,open(cache_path,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    themes=[(k,v.get('name'),set(v.get('members') or [])) for k,v in _cache.items()]
    out=[]
    for _,cs in groups.items():
        mem=set().union(*[cmap.get(x,set()) for x in cs])
        best=None
        for tid,tname,tmem in themes:
            ov=overlap(mem,tmem); j=jac(mem,tmem)
            if ov>=0.45 and (best is None or ov>best[2]): best=(tid,tname,ov,j)
        rows=[x for x in cands if x['concept'] in cs]
        led=max(rows,key=lambda x:x.get('leader_pct') or 0) if rows else {}
        out.append({'concepts':sorted(cs),'n_cluster':len(mem),'max_limit_up':max((x.get('limit_up') or 0) for x in rows) if rows else 0,
                    'max_breadth':max((x.get('breadth') or 0) for x in rows) if rows else 0,
                    'leader':led.get('leader'),'leader_pct':led.get('leader_pct'),
                    'mapped_theme':best[0] if best else None,'mapped_name':best[1] if best else None,
                    'overlap':round(best[2],3) if best else None,'jaccard':round(best[3],3) if best else None})
    out.sort(key=lambda x:(-x['max_limit_up'],-x['max_breadth'],-x['n_cluster']))
    p=os.path.join(ROOT,'data','theme_cluster_candidates.json')
    json.dump({'date':a.date,'threshold':a.threshold,'clusters':out},open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('clusters',len(out))
    for x in out[:20]: print(x)
    print('written',p)
    return 0
if __name__=='__main__': raise SystemExit(main())
