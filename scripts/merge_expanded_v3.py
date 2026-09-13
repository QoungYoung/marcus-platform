# -*- coding: utf-8 -*-
"""合并：用扩充后v2(11事件) + 旧v3里已算好的3个事件fund_ov -> 扩充v3。"""
import pandas as pd, numpy as np
v2=pd.read_csv('data/wolf_theme_features_v2.csv')   # 11事件,含fund_acc
old=pd.read_csv('data/wolf_theme_features_v3.csv')  # 旧5事件,含fund_ov(2022-06-10/2025-02-06/2026-02-26)
print('v2 rows', len(v2), ' old rows', len(old))
# 从old提取已知的fund_ov(仅3个事件有值)
known=old[old['fund_ov_mean'].notna()][['date','theme','fund_ov_mean','fund_ov_max','fund_ov_nstocks']]
look=known.set_index(['date','theme']).to_dict('index')
def get(row):
    k=(str(row['date']), row['theme'])
    v=look.get(k, {})
    return (v.get('fund_ov_mean',np.nan), v.get('fund_ov_max',np.nan), v.get('fund_ov_nstocks',np.nan))
res=v2.apply(lambda r:get(r),axis=1,result_type='expand')
v2['fund_ov_mean']=res[0]; v2['fund_ov_max']=res[1]; v2['fund_ov_nstocks']=res[2]
v2.to_csv('data/wolf_theme_features_v3.csv',index=False)
pd.set_option('display.width',260); pd.set_option('display.max_columns',None)
print('已存扩充v3 data/wolf_theme_features_v3.csv, rows',len(v2))
print('fund_ov有值的事件:', v2[v2['fund_ov_mean'].notna()]['date'].unique().tolist())
print(v2[(v2['wolf']==1)][['date','theme','rs120_ex','fund_acc_5','fund_ov_mean','fund_ov_nstocks']].round(3).to_string(index=False))