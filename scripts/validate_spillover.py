#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""validate_spillover.py — 外溢硬校验：重叠必须由程序算，0 重叠不算外溢。"""
from __future__ import annotations
import argparse, json, sys

ROBOT = ['人形机器人','减速器','机器人执行器']

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--snapshot',default='data/asof/20261028_snapshot.json')
    ap.add_argument('--source',default=','.join(ROBOT))
    ap.add_argument('--target',required=True)
    ap.add_argument('--min-overlap',type=int,default=1)
    a=ap.parse_args()
    s=json.load(open(a.snapshot,encoding='utf-8'))
    cmap=s.get('concepts') or {}
    def members(name):
        return {c for c,v in cmap.items() if name in v}
    src=set()
    for x in a.source.split(','):
        if x.strip(): src |= members(x.strip())
    tgt=members(a.target)
    inter=len(src&tgt)
    ov=inter/min(len(src),len(tgt)) if src and tgt else 0.0
    jac=inter/len(src|tgt) if (src|tgt) else 0.0
    ok=inter>=a.min_overlap
    print(json.dumps({
        'source':a.source,'target':a.target,
        'source_n':len(src),'target_n':len(tgt),
        'overlap_n':inter,'overlap':round(ov,3),'jaccard':round(jac,3),
        'is_spillover':bool(ok),
        'label':'spillover' if ok else 'independent_first_day',
        'reason': '有可验证成员重叠' if ok else '与源题材成员重叠为 0，不能标记为外溢；只能单列为独立首日/轮动',
        'shared':sorted(src&tgt)[:20]
    },ensure_ascii=False,indent=1))
    return 0 if ok else 2

if __name__=='__main__':
    raise SystemExit(main())
