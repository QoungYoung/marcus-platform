#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""theme_sleeve_add.py — 首日 probe 后的二波确认加仓（只输出，不直接下单）。"""
from __future__ import annotations
import argparse, json, os
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',required=True); a=ap.parse_args()
    base=os.path.join(ROOT,'data','mainline')
    ds=sorted(d for d in os.listdir(base) if d.isdigit() and os.path.exists(os.path.join(base,d,'theme_sleeve_facts.json')))
    prev=[d for d in ds if d<a.date]
    if not prev: print('no previous date'); return 0
    pd=prev[-1]
    sel=json.load(open(os.path.join(base,pd,'theme_sleeve_selected.json'),encoding='utf-8'))
    facts=json.load(open(os.path.join(base,a.date,'theme_sleeve_facts.json'),encoding='utf-8'))
    by={t.get('theme_id'):t for t in facts.get('themes') or []}
    add=[]
    for x in sel.get('selected') or []:
        if x.get('entry_type')!='first_day_probe': continue
        t=by.get(x.get('theme_id')) or {}
        u=(t.get('b4') or {}).get('u') or 0
        if (t.get('breadth') or 0)>=0.5 and u>=2 and (t.get('ret1') or 0)>0:
            add.append({'date':a.date,'theme_id':x.get('theme_id'),'symbol':x.get('symbol'),
                        'entry_type':'confirmation_add','size_multiplier':0.5,
                        'breadth':t.get('breadth'),'limit_up':u,'ret1':t.get('ret1')})
    out={'as_of':a.date,'from':pd,'add':add}
    p=os.path.join(base,a.date,'theme_sleeve_add.json'); json.dump(out,open(p,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('written',p,'add',len(add)); [print(x) for x in add]
    return 0
if __name__=='__main__': raise SystemExit(main())
