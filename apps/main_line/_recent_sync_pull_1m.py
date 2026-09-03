# -*- coding: utf-8 -*-
"""recent_sync_pull_1m.py — 拉 159516.SZ / 588170.SH 最近两周 1min (brze)
输出: /app/data/recent_sync/1m/{code6}.json  {YYYYMMDD: [bars]}
"""
import os, sys, json, time
from datetime import date, timedelta
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/app')
from app.services.t_data_sources import fetch_brze_stk_mins
OUT='/app/data/recent_sync/1m'; os.makedirs(OUT, exist_ok=True)
CODES=['159516.SZ','588170.SH']
def weekdays(d0s,d1s):
    d0=date(int(d0s[:4]),int(d0s[4:6]),int(d0s[6:8])); d1=date(int(d1s[:4]),int(d1s[4:6]),int(d1s[6:8]))
    out=[]; d=d0
    while d<=d1:
        if d.weekday()<5: out.append(d.strftime('%Y%m%d'))
        d+=timedelta(days=1)
    return out
for code in CODES:
    data={}; p=os.path.join(OUT, code.split('.')[0]+'.json')
    if os.path.exists(p):
        try: data=json.load(open(p,encoding='utf-8'))
        except: data={}
    todo=[d for d in weekdays('20260818','20260903') if d not in data or len(data[d])<200]
    for td in todo:
        try:
            bars=fetch_brze_stk_mins(code, freq='1min', trade_date=td)
        except Exception as e:
            print('ERR',code,td,str(e)[:80],flush=True); time.sleep(0.5); continue
        if bars: data[td]=bars
        print(code,td,len(bars or []),flush=True); time.sleep(0.35)
    json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False)
    print('SAVED',code,'days',len(data),flush=True)
print('DONE',flush=True)
