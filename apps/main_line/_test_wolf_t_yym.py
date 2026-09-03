# -*- coding: utf-8 -*-
import os, sys, json, time
from datetime import date, timedelta, datetime
sys.path.insert(0, '/app'); sys.path.insert(0, '/app/app')
from app.services.t_data_sources import fetch_brze_stk_mins, fetch_tencent_quote
from app.services.wolf_t_rules import zheng_t_buy_quote, dao_t_sell_quote
OUT='/app/data/recent_sync'
def weekdays(d0s,d1s):
    d0=date(int(d0s[:4]),int(d0s[4:6]),int(d0s[6:8])); d1=date(int(d1s[:4]),int(d1s[4:6]),int(d1s[6:8]))
    out=[]; d=d0
    while d<=d1:
        if d.weekday()<5: out.append(d.strftime('%Y%m%d'))
        d+=timedelta(days=1)
    return out
code='603259.SH'; c6='603259'; p=os.path.join(OUT,c6+'.json'); data={}
if os.path.exists(p):
    try: data=json.load(open(p,encoding='utf-8'))
    except: data={}
todo=[d for d in weekdays('20260818','20260903') if d not in data or len(data[d])<40]
for td in todo:
    try: bars=fetch_brze_stk_mins(code, freq='5min', trade_date=td)
    except Exception as e: print('ERR',td,str(e)[:60],flush=True); time.sleep(0.4); continue
    if bars: data[td]=bars
    print(c6,td,len(bars or []),flush=True); time.sleep(0.3)
json.dump(data,open(p,'w',encoding='utf-8'),ensure_ascii=False)
print('SAVED',c6,len(data),'days',flush=True)
# prev daily (last 5 before today)
def prev_daily(n=5):
    today=datetime.now().strftime('%Y%m%d')
    days=sorted(k for k in data if k<today and data[k])
    out=[]
    for k in days[-n:]:
        bs=sorted(data[k],key=lambda x:str(x.get('time') or x.get('trade_time')))
        out.append({'close':float(bs[-1]['close']),'high':max(float(b['high']) for b in bs),'low':min(float(b['low']) for b in bs),'vol':sum(float(b.get('vol') or 0) for b in bs)})
    return out
# today quote
q=fetch_tencent_quote(['sh603259']).get('sh603259')
print('quote:', q, flush=True)
prev=prev_daily()
print('prev_daily:', json.dumps([{'close':round(x['close'],2),'high':x['high'],'low':x['low'],'vol':round(x['vol'],0)} for x in prev],ensure_ascii=False), flush=True)
if q and prev:
    b,rb=zheng_t_buy_quote(q,prev); d,rd=dao_t_sell_quote(q,prev)
    print('今日(2026-09-03) 药明康德 正T=%s | %s' % (b,rb), flush=True)
    print('                  倒T=%s | %s' % (d,rd), flush=True)
