# -*- coding: utf-8 -*-
import json, os
DATA='/app/data'; OUT='/app/data/recent_sync'
idx={}
for k,v in (json.load(open(os.path.join(DATA,'index_5min_dh.json'),encoding='utf-8')) or {}).items():
    idx[str(k).replace('-','')]=v
for k,v in (json.load(open(os.path.join(OUT,'index5_sina.json'),encoding='utf-8')) or {}).items():
    d=str(k).replace('-','')
    if d not in idx: idx[d]=v
def bs(b): return sorted(b,key=lambda x:str(x.get('time') or x.get('trade_time')))
worst={}
for d in sorted(idx):
    if d<'20260819' or d>'20260903': continue
    bars=bs(idx[d]); prev=None; md=0
    for b in bars:
        c=float(b['close'])
        if prev: md=min(md,(c-prev)/prev*100)
        prev=c
    worst[d]=round(md,3)
print('window index 单根5min最大跌幅%%:')
for d,v in worst.items(): print(d,v)
print('min overall', min(worst.values()))
