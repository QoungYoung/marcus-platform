# -*- coding: utf-8 -*-
"""历史研报回填(report_rc via promax) —— 产业逻辑文本源。按主题代表股拉多年研报落parquet。"""
import os
import requests, warnings, time, json, pandas as pd
warnings.filterwarnings('ignore')
U='https://pcd.mobcvb.cn/tushare/pro'; K=(os.getenv('PROMAX_API_KEY') or os.getenv('PROMAX_KEY') or '').strip()
def ts(a,**p):
    try:
        r=requests.get(f'{U}/{a}',params=p,headers={'X-API-Key':K},verify=False,timeout=45)
        return r.status_code, r.json()
    except Exception as e:
        return None, {'ERR':f'{type(e).__name__}:{str(e)[:100]}'}

THEMES={
  'AI/算力':['300308.SZ','688256.SH','002475.SZ','688981.SH','300502.SZ'],
  '新能源':['300750.SZ','002594.SZ','601012.SH','002460.SZ'],
}
CATS=['发布','突破','政策','扩产','订单','资本开支','新品','中标','量产','投产','获批','招标','涨价','技术','算力','AI','机器人','光模块','消费电子','国产替代']

all_rows=[]
for theme,codes in THEMES.items():
    for code in codes:
        st,d=ts('report_rc', ts_code=code, start_date='20170101', end_date='20260630')
        items=d.get('data',{}).get('items') if isinstance(d.get('data'),dict) else None
        if not items:
            print(f'  {theme} {code}: 0'); continue
        for it in items:
            # fields: ts_code,name,report_date,report_title,report_type,classify,org_name,author_name,...
            tcode=it[0]; nm=it[1]; rd=it[2]; title=it[3] if len(it)>3 else ''
            org=it[6] if len(it)>6 else ''; rt=it[4] if len(it)>4 else ''
            all_rows.append(dict(ts_code=tcode,name=nm,report_date=rd,title=title,report_type=rt,org_name=org,theme=theme,cat=int(any(k in title for k in CATS))))
        print(f'  {theme} {code}: {len(items)} rows')
        time.sleep(0.3)
df=pd.DataFrame(all_rows)
df.to_parquet('data/report_rc.parquet')
pd.set_option('display.width',220); pd.set_option('display.max_columns',None)
print('\n已存 data/report_rc.parquet 行数', len(df), ' 主题:', df['theme'].value_counts().to_dict())
print(df.groupby(['theme']).agg(n=('title','size'), cat=('cat','sum')).to_string())