# -*- coding: utf-8 -*-
import os
from datetime import datetime
print('worker now=', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
import requests, urllib3
urllib3.disable_warnings()
PM='https://pcd.mobcvb.cn/tushare/pro'; PK=(os.getenv('PROMAX_API_KEY') or os.getenv('PROMAX_KEY') or '').strip()
for start,end in [('20250101','20260903'),('20260818','20260903')]:
    try:
        r=requests.get(f'{PM}/sw_daily',params={'ts_code':'801080.SI','start_date':start,'end_date':end},headers={'X-API-Key':PK},verify=False,timeout=30)
        dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
        print('start=%s end=%s -> n=%d fields=%s' % (start,end,len(it),f))
        if it: print('   first=%s' % it[0])
    except Exception as e:
        print('start=%s end=%s ERR=%s' % (start,end,e))
