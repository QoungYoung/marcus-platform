# -*- coding: utf-8 -*-
# 前向回测: 对历史时点跑 wave_agent, 记录 level 与后续 20/60 日指数收益
import sys, json, time
sys.path.insert(0, '/tmp')
import wave_agent as wa
import pandas as pd
close=wa.load_close()
dates=[pd.Timestamp(x) for x in ['2022-01-25','2022-06-10','2025-02-06','2025-07-11','2025-12-01','2026-01-12','2026-02-26','2026-04-15','2026-06-26']]
idx=close.index
print('date | level | sub | conf | f20% | f60%')
for d in dates:
    pos=idx.searchsorted(d)
    if pos>=len(close): continue
    f20 = close.iloc[pos+20]/close.iloc[pos]-1 if pos+20<len(close) else None
    f60 = close.iloc[pos+60]/close.iloc[pos]-1 if pos+60<len(close) else None
    f=wa.index_features(str(d.date()))
    if f is None: print(d.date(),'no data'); continue
    try:
        reply=wa.call_agent(wa.build_prompt(f), session='fw'+str(d.date()).replace('-',''))
        res=wa.parse(reply)
    except Exception as e:
        res={'level':'ERR','sub_level':str(e)[:40]}
    print(f"{d.date()} | {res.get('level')} | {res.get('sub_level','')} | {res.get('confidence')} | {'%.1f'%(f20*100) if f20 is not None else 'NA'}% | {'%.1f'%(f60*100) if f60 is not None else 'NA'}%")
    time.sleep(0.3)
