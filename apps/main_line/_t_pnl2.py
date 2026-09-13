# -*- coding: utf-8 -*-
import os, json
D='/app/data/recent_sync'
def bars(code):
    p=os.path.join(D,code+'.json')
    try: d=json.load(open(p,encoding='utf-8'))
    except: return []
    return sorted(d.get('20260903',[]), key=lambda x:str(x.get('time') or x.get('trade_time')))
def calc(code, thr=2.5):
    bs=bars(code)
    if len(bs)<6: return None
    hi=max(float(b['high']) for b in bs)
    run_low=10**18; buy=None; buy_idx=None
    for i,b in enumerate(bs):
        run_low=min(run_low, float(b['low']))
        if hi>0 and (run_low/hi-1)*100 <= -thr:
            buy=float(b['low']); buy_idx=i; break
    if buy is None: return None
    day_hi=hi
    sell=buy; sell_idx=buy_idx
    for j in range(buy_idx+1, len(bs)):
        h=float(bs[j]['high'])
        if h>sell: sell=h; sell_idx=j
        if sell_idx>buy_idx and j>=sell_idx+2 and float(bs[j]['close']) < float(bs[sell_idx]['close']): break
    return {'code':code,'buy':round(buy,3),'buy_t':str(bs[buy_idx].get('time') or '')[:16],
            'day_high':round(day_hi,3),'first_peak_sell':round(sell,3),'first_peak_pnl':round((sell/buy-1)*100,2),
            'to_day_high_pnl':round((day_hi/buy-1)*100,2)}
for c in ['603259','159516','588170']:
    print(calc(c))
