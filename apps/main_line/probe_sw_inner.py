# -*- coding: utf-8 -*-
import os
import time, requests, urllib3, traceback
urllib3.disable_warnings()
from datetime import datetime
PM='https://pcd.mobcvb.cn/tushare/pro'; PK=(os.getenv('PROMAX_API_KEY') or os.getenv('PROMAX_KEY') or '').strip()
idx_code='801080.SI'
try:
    end=datetime.now().strftime('%Y%m%d')
    print('end=',end)
    r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx_code,'start_date':'20250101','end_date':end},headers={'X-API-Key':PK},verify=False,timeout=30)
    print('http status', r.status_code, 'len', len(r.text))
    dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    print('n', len(it), 'fields', f)
    fmap={f[i]:i for i in range(len(f))} if f else {}
    out={str(x[fmap['trade_date']]).replace('-',''):float(x[fmap['high']]) for x in it if 'trade_date' in fmap and 'high' in fmap}
    print('out len', len(out), 'sample', list(out.items())[-2:])
except Exception:
    traceback.print_exc()
