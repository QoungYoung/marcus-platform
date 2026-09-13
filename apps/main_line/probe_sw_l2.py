
import os
import requests, urllib3, json, urllib.parse
urllib3.disable_warnings()
DH='http://datahubco.com/app-api/openapi/v1/tushare'; DK='dba548a206a453c197f9175189b757374fa6db9554bb29e69efea127'
PM='https://pcd.mobcvb.cn/tushare/pro'; PK=(os.getenv('PROMAX_API_KEY') or os.getenv('PROMAX_KEY') or '').strip()
def member(sym):
    r=requests.get(f'{DH}/index_member_all',params={'ts_code':sym},headers={'X-API-Key':DK},timeout=40)
    dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    if not it or not f: return None
    m={f[i]:it[0][i] for i in range(len(f))}
    return m
def sw_high(idx,s,e):
    r=requests.get(f'{PM}/sw_daily',params={'ts_code':idx,'start_date':s,'end_date':e},headers={'X-API-Key':PK},verify=False,timeout=30)
    dd=r.json().get('data') or {}; it=dd.get('items') or []; f=dd.get('fields') or []
    fm={f[i]:i for i in range(len(f))}
    return {str(x[f['index'] if False else fm['trade_date']]).replace('-',''):float(x[fm['high']]) for x in it} if fm and it else {}
PROX={'603259':'SH','301018':'SZ','688981':'SH','002945':'SZ','002165':'SZ'}
for c,suf in PROX.items():
    m=member(c+'.'+suf)
    print(c, {k:m.get(k) for k in ('name','l1_code','l2_code','l3_code') if m})
print()
# 用户resolve给的是二级(801156医疗服务). 看二级行业在08-27是否近高
for idx,label in [('801156.SI','医疗服务'),('801081.SI','半导体'),('801193.SI','证券Ⅱ'),('801072.SI','通用设备'),('801034.SI','化学制品')]:
    d=sw_high(idx,'20260818','20260903')
    if not d: print(label,idx,'NO DATA'); continue
    ks=sorted(d); print('%s %s' % (label,idx))
    for k in ks: print('   %s %.2f' % (k,d[k]))
