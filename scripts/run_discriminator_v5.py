# -*- coding: utf-8 -*-
# 判别器v5：catalyst主导(候选) + 动量/资金确认(两档)
import pandas as pd, json, numpy as np
df=pd.read_csv('data/wolf_theme_features_v5.csv')
def z(s): return (s-s.mean())/s.std() if s.std() else pd.Series(0.0,index=s.index)
def base_score(g):
    return (z(g['rs120_ex'])*1.0 + z(g['rs60_ex'])*0.8 + z(g['lead60_ex'])*0.6 + z(g['lead30_ex'])*0.6
            + g['trend']*0.5 + g['breakout']*0.3 + g['higher_low']*0.2 + z(g['vol_exp'])*0.5
            + z(g['fund_acc_5'].fillna(0))*0.3 + z(g['fund_ov_mean'].fillna(0))*0.3)
rows=[]
for ev,g in df.groupby('date'):
    g=g.copy()
    g['base']=base_score(g)
    g['score']=z(g['catalyst_score'].fillna(0))*1.0 + 0.3*z(g['base'])
    # 两档
    g['candidate']=g['catalyst_score'].fillna(0)>=0.7
    g['confirmed']=(g['candidate']) & (g['base']>0)
    g=g.sort_values('score',ascending=False).reset_index(drop=True)
    wolf=g[g['wolf']==1]
    wr=wolf.index[0]+1 if len(wolf) else None
    wc=wolf['candidate'].iloc[0] if len(wolf) else None
    wf=wolf['confirmed'].iloc[0] if len(wolf) else None
    rows.append({'event':ev,'wolf_rank':wr,'candidate':wc,'confirmed':wf,'top_pred':list(g.head(3)['theme'])})
r=pd.DataFrame(rows)
pd.set_option('display.width',240)
print(r.to_string(index=False))
ranks=r['wolf_rank'].dropna().astype(int)
print()
print('狼大排名:', ranks.tolist())
print('top1:', round((ranks<=1).mean()*100,0),'%  top3:', round((ranks<=3).mean()*100,0),'%')
print('狼大被纳入候选(catalyst>=0.7):', round(r['candidate'].fillna(False).mean()*100,0),'%')
print('狼大被确认(candidate&动量资金>0):', round(r['confirmed'].fillna(False).mean()*100,0),'%')
