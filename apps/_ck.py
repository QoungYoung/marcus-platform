# -*- coding: utf-8 -*-
import urllib.request, json
body = json.dumps({'symbol': 'SH603259', 'price': 156.28, 'total_assets': 250000}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/api/v1/indicator/calc-position', data=body, headers={'Content-Type': 'application/json'}, method='POST')
d = json.loads(urllib.request.urlopen(req, timeout=15).read().decode())
sl = d.get('stop_loss', {})
print('dynamic_stop_pct:', sl.get('dynamic_stop_pct'), '| hard_stop_price:', sl.get('hard_stop_price'))
print('warnings wolf:', [w for w in d.get('warnings', []) if '止损参考' in w])