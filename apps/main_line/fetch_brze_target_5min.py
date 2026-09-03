# -*- coding: utf-8 -*-
"""fetch_brze_target_5min.py — brze 拉目标股 5min（分20日chunk防IncompleteRead, 断点续拉）
输出: data/stock_5m_bt/<code6>.json  {date: [bars]}
日期窗: 2025-11-24 ~ 2026-07-24 (覆盖 E05/E06/E08/E12/E13 ±窗口)
用法: python -u apps/main_line/fetch_brze_target_5min.py
"""
import os, sys, json, time
from datetime import date, timedelta
for _p in ("/app", "/app/core", "/app/app"):
    if _p not in sys.path: sys.path.insert(0, _p)
from app.services.t_data_sources import fetch_brze_stk_mins

CODES = ["002837.SZ","301018.SZ","300499.SZ","300308.SZ","300502.SZ","300394.SZ",
         "688981.SH","688041.SH","002371.SZ","603501.SH","688012.SH","600584.SH",
         "688072.SH","300054.SZ","300236.SZ","601138.SH","000977.SZ","000938.SZ",
         "603019.SH","000034.SZ"]
D0 = date(2025,11,24); D1 = date(2026,7,24)
def chunks():
    d=D0
    while d<=D1:
        e=min(d+timedelta(days=19), D1)
        yield d.strftime('%Y%m%d'), e.strftime('%Y%m%d')
        d=e+timedelta(days=1)
CH = list(chunks())
print('chunks',len(CH),CH[0],CH[-1],flush=True)
for code in CODES:
    out="/app/data/stock_5m_bt/%s.json"%code[:6]
    os.makedirs(os.path.dirname(out),exist_ok=True)
    try: data=json.load(open(out,encoding='utf-8'))
    except Exception: data={}
    for s,e in CH:
        # 判断是否已有该窗全部(以 s 前缀日期粗判: 存在>=1/3 天数即认为拉过)
        got=[k for k in data if s<=k<=e]
        if len(got)>=5: continue
        for attempt in range(2):
            try:
                bars=fetch_brze_stk_mins(code,freq='5min',start_date=s,end_date=e)
            except Exception as ex:
                print(code,s,e,'ERR',str(ex)[:80],flush=True); time.sleep(2); bars=None
            if bars:
                for b in bars:
                    t=str(b.get('time') or '')[:10].replace('-','')
                    if t: data.setdefault(t,[]).append(b)
                break
            time.sleep(2)
        json.dump(data,open(out,'w',encoding='utf-8'),ensure_ascii=False)
        time.sleep(0.4)
    # 内部按日期排序去重
    for k in data:
        seen=set(); u=[]
        for b in sorted(data[k],key=lambda x:str(x.get('time'))):
            key=str(b.get('time'))+str(b.get('open'))+str(b.get('close'))
            if key in seen: continue
            seen.add(key); u.append(b)
        data[k]=u
    json.dump(data,open(out,'w',encoding='utf-8'),ensure_ascii=False)
    print('DONE',code,'days',len(data),flush=True)
print('ALL DONE',flush=True)
