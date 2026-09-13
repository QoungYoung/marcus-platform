# -*- coding: utf-8 -*-
# 回填9主题代表股 report_rc 研报 -> report_rc_all.parquet
import os
import requests, warnings, time, json, pandas as pd
warnings.filterwarnings('ignore')
U='https://pcd.mobcvb.cn/tushare/pro'; K=(os.getenv('PROMAX_API_KEY') or os.getenv('PROMAX_KEY') or '').strip()
def ts(a,**p):
    for _ in range(2):
        try:
            r=requests.get(f'{U}/{a}',params=p,headers={'X-API-Key':K},verify=False,timeout=45)
            return r.status_code, r.json()
        except Exception as e:
            time.sleep(1)
    return None, {'ERR':'x'}
THEMES={
 'AI/算力/科技':['300308.SZ','002475.SZ','300502.SZ'],
 '半导体/芯片':['688981.SH','002371.SZ','603986.SH'],
 '新能源/电池':['300750.SZ','002594.SZ','601012.SH'],
 '军工/航天':['600760.SH','600893.SH','000768.SZ'],
 '资源/周期':['601899.SH','601088.SH','600028.SH'],
 '金融':['600030.SH','601318.SH','600036.SH'],
 '消费/内需':['600519.SH','000858.SZ','000333.SZ'],
 '医药':['600276.SH','300760.SZ','600196.SH'],
 '稳增长/基建':['601668.SH','600585.SH','601390.SH'],
}
rows=[]
for theme,codes in THEMES.items():
    for code in codes:
        st,d=ts('report_rc',ts_code=code,start_date='20200101',end_date='20260630')
        items=d.get('data',{}).get('items') if isinstance(d.get('data'),dict) else None
        n=len(items) if items else 0
        print(f'{theme} {code}: {n}', flush=True)
        if not items: continue
        for it in items:
            tcode=it[0]; nm=it[1]; rd=it[2]; title=it[3] if len(it)>3 else ''
            org=it[6] if len(it)>6 else ''
            rows.append(dict(ts_code=tcode,name=nm,report_date=rd,title=title,org_name=org,theme=theme))
        time.sleep(0.2)
df=pd.DataFrame(rows)
df.to_parquet('data/report_rc_all.parquet')
print('已存 data/report_rc_all.parquet 行数', len(df))
print('主题×行:', df['theme'].value_counts().to_dict())
