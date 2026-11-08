#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""backfill_theme_membership.py — 把当前题材成员灌入 PG theme_membership。"""
from __future__ import annotations
import argparse, os, sys
ROOT=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT,os.path.join(ROOT,'jobs'),os.path.join(ROOT,'backend'),os.path.join(ROOT,'core')):
    if p not in sys.path: sys.path.insert(0,p)
from app.services.theme_universe_store import load_theme_config, save_members
from stock_agent_facts import members_for


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--date',default='20261102'); a=ap.parse_args()
    cfg=load_theme_config(); total=0
    for t in (cfg.get('themes') or [])+(cfg.get('satellite_themes') or []):
        tid=t.get('id'); 
        try: mem=set(members_for(t,a.date))
        except Exception as e: print('ERR',tid,e); continue
        if mem:
            n=save_members(tid,mem,a.date,source='sources'); total+=n
            print(tid,len(mem),'saved')
    print('total',total)
    return 0
if __name__=='__main__': raise SystemExit(main())
