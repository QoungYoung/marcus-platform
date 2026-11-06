#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_ab_gate.py — 把 A/B 结果接到题材 selected。

A: {"source_state":"","need_switch":true}
B: {"target":"","roles":{"leader":""}}
"""
from __future__ import annotations
import argparse,json,os,re
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIAS={'商业航天':'space','航天':'space','卫星互联网':'satellite','AI应用':'ai_app'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True)
    ap.add_argument('--a',required=True); ap.add_argument('--b',required=True); a=ap.parse_args()
    A=json.load(open(a.a,encoding='utf-8')); B=json.load(open(a.b,encoding='utf-8'))
    base=os.path.join(ROOT,'data','mainline',a.date)
    p=os.path.join(base,'theme_sleeve_selected.json')
    sel=json.load(open(p,encoding='utf-8')) if os.path.exists(p) else {'selected':[]}
    tid=ALIAS.get(B.get('target'),B.get('target'))
    leader=(B.get('roles') or {}).get('leader')
    if not (isinstance(leader,str) and re.match(r'^\d{6}\.(SH|SZ|BJ)$',leader)):
        leader=None
    if not A.get('need_switch'):
        out={'as_of':a.date,'sleeve':'theme','selected':[],'candidates':0,
             'ab_gate':{'a':A,'b':B,'reason':'A_need_switch_false'}}
    else:
        # B 的 leader 优先，不能用 select 里旧 leader 覆盖
        keep=[{'date':a.date,'sleeve':'theme','theme_id':tid,'theme_name':B.get('target'),
               'symbol':leader,'role':'leader','tier':'ab','first_linkage':True,
               'entry_type':'first_day_probe','size_multiplier':0.5}] if leader else []
        out={'as_of':a.date,'sleeve':'theme','selected':keep,'candidates':len(keep),
             'ab_gate':{'a':A,'b':B,'reason':'A_need_switch_true' if leader else 'B_leader_not_stock_code'}}
    json.dump(out,open(os.path.join(base,'theme_sleeve_selected.json'),'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('AB gate',a.date,'need_switch',A.get('need_switch'),'target',B.get('target'),'leader',leader,'selected',[(x.get('theme_id'),x.get('symbol')) for x in out['selected']])
if __name__=='__main__': main()
