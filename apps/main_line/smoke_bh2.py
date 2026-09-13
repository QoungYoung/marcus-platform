# -*- coding: utf-8 -*-
import sys; sys.path.insert(0,'/app')
from app.services.wolf_discipline import board_half
import json, datetime
def run(sym, cur, pre, cost):
    bh = board_half(json.dumps({"positions":[{"symbol":sym,"avg_cost":cost,"volume":1000}]}), datetime.datetime.now(),
                    quotes={sym:{"current":cur,"pre_close":pre}})
    return bool(bh.get('active_sells')), (bh.get('active_sells') or [{}])[0].get('reason','')[:50]
print('SZ300460 cur=13.0 pre=10.5 cost=10 (20%板触发):', run('SZ300460',13.0,10.5,10.0))
print('SH600519 cur=11.0 pre=10.0 cost=10 (10%板触发):', run('SH600519',11.0,10.0,10.0))
print('SH600519 cur=10.4 pre=10.0 cost=10 (未达板):', run('SH600519',10.4,10.0,10.0))
