# -*- coding: utf-8 -*-
# 把全9主题agent catalyst并入特征表v5，并用catalyst+fund+rs重跑判别器
import pandas as pd, json, numpy as np
df=pd.read_csv('data/wolf_theme_features_v3.csv')
res=json.load(open('data/wolf_events_agent_all.json',encoding='utf-8'))
key={}
for ev in res:
    for th,vv in ev['themes'].items():
        sc=vv.get('catalyst_score') if isinstance(vv,dict) else None
        st=vv.get('main_line_stage') if isinstance(vv,dict) else ''
        key[(ev['date'],th)]=(sc,st)
df['catalyst_score']=df.apply(lambda x: key.get((str(x['date']),x['theme']), (None,''))[0], axis=1)
df['main_line_stage']=df.apply(lambda x: key.get((str(x['date']),x['theme']), (None,''))[1], axis=1)
df.to_csv('data/wolf_theme_features_v5.csv', index=False)

# 判别器：基础分(rs+structure+fund_acc+fund_ov) + 0.6*catalyst_score
def z(s): return (s-s.mean())/s.std() if s.std() else pd.Series(0.0,index=s.index)
rows=[]
for ev,g in df.groupby('date'):
    g=g.copy()
    base=(z(g['rs120_ex'])*1.0 + z(g['rs60_ex'])*0.8 + z(g['lead60_ex'])*0.6 + z(g['lead30_ex'])*0.6
          + g['trend']*0.5 + g['breakout']*0.3 + g['higher_low']*0.2 + z(g['vol_exp'])*0.5
          + z(g['fund_acc_5'].fillna(0))*0.3 + z(g['fund_ov_mean'].fillna(0))*0.3)
    g['score']=base + z(g['catalyst_score'].fillna(0))*0.6
    g=g.sort_values('score',ascending=False).reset_index(drop=True)
    wr=g[g['wolf']==1].index[0]+1 if (g['wolf']==1).any() else None
    rows.append({'event':ev,'wolf_rank':wr,'top_pred':list(g.head(3)['theme'])})
r=pd.DataFrame(rows)
pd.set_option('display.width',220)
print(r.to_string(index=False))
ranks=r['wolf_rank'].dropna().astype(int)
print()
print('狼大主线排名:', ranks.tolist())
print('top1:', round((ranks<=1).mean()*100,0),'%  top3:', round((ranks<=3).mean()*100,0),'% (随机top1≈11%/top3≈33%)')
print('已存 data/wolf_theme_features_v5.csv')
