# -*- coding: utf-8 -*-
import os, sys, json, time
from datetime import date, timedelta
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/app')
from app.services.t_data_sources import fetch_brze_stk_mins
OUT='/app/data/recent_sync'
CODES=['601138.SH','000977.SZ','300308.SZ','002371.SZ','688981.SH']
def weekdays(d0s,d1s):
    d0=date(int(d0s[:4]),int(d0s[4:6]),int(d0s[6:8])); d1=date(int(d1s[:4]),int(d1s[4:6]),int(d1s[6:8]))
    out=[]; d=d0
    while d<=d1:
        if d.weekday()<5: out.append(d.strftime('%Y%m%d'))
        d+=timedelta(days=1)
    return out
for code in CODES:
    c6=code.split('.')[0]; p=os.path.join(OUT,c6+'.json'); data={}
    if os.path.exists(p):
        try: data=json.load(open(p,encoding='utf-8'))
        except: data={}
    todo=[d for d in weekdays('20260818','20260903') if d not in data or len(data[d])<40]
    for td in todo:
        try: bars=fetch_brze_stk_mins(code, freq='5min', trade_date=td)
        except Exception as e: print('ERR',c6,td,str(e)[:60],flush=True); time.sleep(0.4); continue
        if bars: data[td]=bars
        print(c6,td,len(bars or []),flush=True); time.sleep(0.3)
    json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False)
    print('SAVED',c6,len(data),flush=True)
print('DONE',flush=True)
