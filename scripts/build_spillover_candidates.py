#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""build_spillover_candidates.py — 从 B 数据包过滤同链/主线下跌，生成 B 主候选。"""
from __future__ import annotations
import argparse, json, os
import yaml
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--b',default='data/asof/20261028_b_snapshot.json')
    ap.add_argument('--out',default='data/asof/20261028_b_candidates.json')
    ap.add_argument('--config',default='config/mainline/spillover_chain_filter.yaml')
    a=ap.parse_args()
    d=json.load(open(a.b,encoding='utf-8'))
    cfg=yaml.safe_load(open(a.config,encoding='utf-8')) or {}
    same=list(cfg.get('same_chain') or []); down=list(cfg.get('mainline_down') or []); generic=list(cfg.get('generic') or [])
    keep=[]; excluded=[]
    for c in d.get('candidates') or []:
        n=c.get('concept')
        if any(x in n or n in x for x in same):
            excluded.append({'concept':n,'reason':'same_chain','overlap':c.get('overlap_n')}); continue
        if any(x in n or n in x for x in down):
            excluded.append({'concept':n,'reason':'mainline_down','overlap':c.get('overlap_n')}); continue
        if any(x in n for x in generic):
            excluded.append({'concept':n,'reason':'generic','overlap':c.get('overlap_n')}); continue
        keep.append(c)
    d['candidates']=keep
    d['excluded']=excluded
    d['filter_config']=a.config
    json.dump(d,open(a.out,'w',encoding='utf-8'),ensure_ascii=False)
    print(json.dumps({'ok':True,'keep':len(keep),'excluded':len(excluded),'out':a.out},ensure_ascii=False))
if __name__=='__main__': main()
