# -*- coding: utf-8 -*-
"""recent_sync_pull.py — 最近两周(2026-08-19~09-03) wolf 实际持仓 5min + 指数5min(新浪)
输出: /app/data/recent_sync/{code6}.json (YYYYMMDD -> bars) ; index5_sina.json (YYYY-MM-DD -> bars)
"""
import os, sys, json, time, requests
from datetime import date, timedelta
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/app')
from app.services.t_data_sources import fetch_brze_stk_mins
DATA='/app/data'; OUT='/app/data/recent_sync'
os.makedirs(OUT, exist_ok=True)
CODES=['159516.SZ','588170.SH','301018.SZ','002165.SZ','603757.SH','002945.SZ']

def weekdays(d0s,d1s):
    d0=date(int(d0s[:4]),int(d0s[4:6]),int(d0s[6:8])); d1=date(int(d1s[:4]),int(d1s[4:6]),int(d1s[6:8]))
    out=[]; d=d0
    while d<=d1:
        if d.weekday()<5: out.append(d.strftime('%Y%m%d'))
        d+=timedelta(days=1)
    return out

def days_for(code):
    # merge existing stock_5m_bt then pull missing >= 08-19
    p=os.path.join(OUT, code.split('.')[0]+'.json')
    data={}
    merge=os.path.join(DATA,'stock_5m_bt',code.split('.')[0]+'.json')
    if os.path.exists(merge):
        try: data=json.load(open(merge,encoding='utf-8'))
        except: data={}
    want=weekdays('20260818','20260903')
    todo=[w for w in want if w not in data or (not data[w] or len(data[w])<40)]
    if not todo:
        json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False); print(code,'ALL COVERED',len(data),flush=True); return
    for td in todo:
        try:
            bars=fetch_brze_stk_mins(code, freq='5min', trade_date=td)
        except Exception as e:
            print('ERR',code,td,str(e)[:90],flush=True); time.sleep(0.4); continue
        if bars: data[td]=bars
        print(code,td,len(bars or []),flush=True)
        time.sleep(0.35)
    json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False)
    print('SAVED',code,'days',len(data),flush=True)

for c in CODES:
    days_for(c)

# ---- index 5min via Sina (recent ~ until 09-03) ----
UA={"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
url="https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20x=/CN_MarketDataService.getKLineData?symbol=sh000001&scale=5&ma=no&datalen=1023"
try:
    raw=requests.get(url,headers=UA,timeout=20).text
    s=raw.find('(['); e=raw.rfind('])')
    import json as _j
    arr=_j.loads(raw[s+1:e+1])
    idx={}
    for r in arr:
        day=str(r['day'])[:10]
        if day<'2026-08-19': continue
        bar={'time':str(r['day']),'open':float(r['open']),'close':float(r['close']),
             'high':float(r['high']),'low':float(r['low']),'vol':float(r['volume']),
             'amount':float(r.get('amount') or 0)}
        idx.setdefault(day,[]).append(bar)
    for d in idx: idx[d].sort(key=lambda b:b['time'])
    json.dump(idx,open(os.path.join(OUT,'index5_sina.json'),'w',encoding='utf-8'),ensure_ascii=False)
    print('INDEX sina days',len(idx),sorted(idx)[0],sorted(idx)[-1],flush=True)
except Exception as ex:
    print('INDEX ERR',type(ex).__name__,str(ex)[:120],flush=True)
print('PULL DONE',flush=True)
