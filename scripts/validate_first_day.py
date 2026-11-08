#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""validate_first_day.py — 外溢首日硬校验：prior_launch_days 非空直接失败。"""
from __future__ import annotations
import argparse, json, sys

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--b-snapshot',default='data/asof/20261028_b_snapshot.json')
    ap.add_argument('--target',required=True)
    a=ap.parse_args()
    d=json.load(open(a.b_snapshot,encoding='utf-8'))
    c=next((x for x in d.get('candidates') or [] if x.get('concept')==a.target),None)
    if not c:
        print(json.dumps({'target':a.target,'ok':False,'error':'candidate_not_found'},ensure_ascii=False)); return 3
    first=bool(c.get('first_day_absolute'))
    prior=c.get('prior_launch_days') or []
    print(json.dumps({'target':a.target,'first_day_absolute':first,'prior_launch_days':prior,
                      'launch_days':c.get('launch_days') or [],
                      'ok':first,'label':'first_day' if first else 'second_wave',
                      'reason':'过去10日无板块级启动' if first else '过去10日已启动过，禁止作为外溢首日主目标'},ensure_ascii=False,indent=1))
    return 0 if first else 2
if __name__=='__main__': raise SystemExit(main())
