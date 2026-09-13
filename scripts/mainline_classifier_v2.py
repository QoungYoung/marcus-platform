# -*- coding: utf-8 -*-
"""主线判别器v2：动量+结构+资金蓄积(fund_acc)+公募重仓(fund_ov)加权组合分,按事件排序。"""
import pandas as pd, numpy as np
df=pd.read_csv('data/wolf_theme_features_v3.csv')
def z(s):
    return (s-s.mean())/s.std() if s.std() else pd.Series(0.0,index=s.index)
def score_theme(g):
    s=z(g['rs120_ex'])*1.0 + z(g['rs60_ex'])*0.8 + z(g['lead60_ex'])*0.6 + z(g['lead30_ex'])*0.6
    s=s + g['trend']*0.5 + g['breakout']*0.3 + g['higher_low']*0.2 + z(g['vol_exp'])*0.5
    # 并入资金蓄积与公募重仓(NaN→0)
    s=s + z(g['fund_acc_5'].fillna(0))*0.3 + z(g['fund_ov_mean'].fillna(0))*0.3
    return s
rows=[]
for ev, g in df.groupby('date'):
    g=g.copy(); g['score']=score_theme(g)
    g=g.sort_values('score',ascending=False).reset_index(drop=True)
    wr=g[g['wolf']==1].index[0]+1 if (g['wolf']==1).any() else None
    rows.append(dict(event=ev, wolf_theme=g[g['wolf']==1]['theme'].iloc[0] if (g['wolf']==1).any() else '', wolf_rank=wr, top_pred=list(g.head(3)['theme'])))
r=pd.DataFrame(rows)
pd.set_option('display.width',230)
print(r.to_string(index=False))
ranks=r['wolf_rank'].dropna().astype(int)
print()
print('狼大主线排名:', ranks.tolist())
print('top1命中:', round((ranks<=1).mean()*100,0),'%   top3命中:', round((ranks<=3).mean()*100,0),'%  (9主题随机top3≈33%)')