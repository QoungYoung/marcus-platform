#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_cluster_stability.py — 候选概念成员重叠聚类阈值稳定性（快速，不取题材成员）。"""
from __future__ import annotations
import argparse, json, os, sys
from collections import defaultdict
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def ov(a,b):
    return len(a&b)/min(len(a),len(b)) if a and b else 0.0

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102')
    a=ap.parse_args()
    disc=json.load(open(os.path.join(ROOT,'data','theme_market_discovery.json'),encoding='utf-8'))
    cands=disc.get('candidates') or []; names=[x['concept'] for x in cands]
    import psycopg2
    c=psycopg2.connect(os.environ['DATABASE_URL']); cur=c.cursor()
    cur.execute("select concept_name,array_agg(ts_code) from stock_concept_map where concept_name = ANY(%s) group by concept_name",(names,))
    cmap={str(r[0]):set(str(x) for x in r[1]) for r in cur.fetchall()}; c.close()
    res={}
    for th in (0.45,0.55,0.60,0.65):
        parent=list(range(len(names)))
        def find(x):
            while parent[x]!=x: parent[x]=parent[parent[x]]; x=parent[x]
            return x
        for i,n in enumerate(names):
            for j in range(i+1,len(names)):
                if ov(cmap.get(n,set()),cmap.get(names[j],set()))>=th:
                    ri,rj=find(i),find(j)
                    if ri!=rj: parent[rj]=ri
        groups=defaultdict(list)
        for n in names: groups[find(names.index(n))].append(n)
        rows=[]
        for cs in groups.values():
            xs=[x for x in cands if x['concept'] in cs]
            rows.append({'concepts':cs,'n_cluster':len(set().union(*[cmap.get(x,set()) for x in cs])),
                         'limit_up':max((x.get('limit_up') or 0) for x in xs) if xs else 0,
                         'breadth':max((x.get('breadth') or 0) for x in xs) if xs else 0,
                         'leader':max(xs,key=lambda x:x.get('leader_pct') or 0).get('leader') if xs else None})
        rows.sort(key=lambda x:(-x['limit_up'],-x['n_cluster']))
        res[str(th)]={'clusters':len(rows),'top':rows[:10]}
    payload={'date':a.date,'stability':res}
    json.dump(payload,open(os.path.join(ROOT,'data','theme_cluster_stability.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    try:
        from theme_candidate_store import save
        save(a.date,'cluster_stability',payload)
    except Exception as e: print('[pg] cluster_stability save fail',str(e)[:100])
    for th,v in res.items():
        print('threshold',th,'clusters',v['clusters'])
        for x in v['top'][:4]: print(' ',x['concepts'][:4],'n',x['n_cluster'],'lim',x['limit_up'])
    return 0
if __name__=='__main__': raise SystemExit(main())
