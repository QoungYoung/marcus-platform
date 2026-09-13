# -*- coding: utf-8 -*-
# 把 agent 产业催化分/主线阶段并入特征表 -> v4
import pandas as pd, json, re
df=pd.read_csv('data/wolf_theme_features_v3.csv')
res=json.load(open('data/wolf_events_agent_result.json',encoding='utf-8'))
def stage_of(j):
    j=j or ''
    if '已发酵' in j: return '已发酵'
    if '确认' in j: return '确认'
    if '候选' in j or '早候选' in j: return '早候选'
    return ''
key={}
for r in res:
    key[(r['date'], r['theme'])] = (r.get('catalyst_score'), stage_of(r.get('main_line_judgment','')), r.get('main_line_judgment',''))
df['catalyst_score']=df.apply(lambda x: key.get((str(x['date']),x['theme']), (None,'',''))[0], axis=1)
df['main_line_stage']=df.apply(lambda x: key.get((str(x['date']),x['theme']), (None,'',''))[1], axis=1)
df['catalyst_judgment']=df.apply(lambda x: key.get((str(x['date']),x['theme']), (None,'',''))[2], axis=1)
df.to_csv('data/wolf_theme_features_v4.csv', index=False)
pd.set_option('display.width',260); pd.set_option('display.max_columns',None)
print('已存 data/wolf_theme_features_v4.csv 行数', len(df), ' 新增列:', ['catalyst_score','main_line_stage','catalyst_judgment'])
print(df[df['wolf']==1][['date','theme','rs120_ex','fund_acc_5','fund_ov_mean','catalyst_score','main_line_stage']].round(3).to_string(index=False))
