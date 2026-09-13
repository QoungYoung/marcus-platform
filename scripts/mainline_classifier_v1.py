# -*- coding: utf-8 -*-
"""主线判别器v1(加权组合分)：用v2特征对每个事件把主题排序,看狼大主线排第几。"""
import pandas as pd, numpy as np
df=pd.read_csv('data/wolf_theme_features_v3.csv')
feat=['rs120_ex','rs60_ex','lead60_ex','lead30_ex','trend','breakout','higher_low','vol_exp']
def z(s):
    return (s-s.mean())/s.std() if s.std() else pd.Series(0.0,index=s.index)
def score_theme(g):
    s=z(g['rs120_ex'])*1.0 + z(g['rs60_ex'])*0.8 + z(g['lead60_ex'])*0.6 + z(g['lead30_ex'])*0.6
    s=s + g['trend']*0.5 + g['breakout']*0.3 + g['higher_low']*0.2 + z(g['vol_exp'])*0.5
    return s
rows=[]
for ev, g in df.groupby('date'):
    g=g.copy(); g['score']=score_theme(g)
    g=g.sort_values('score',ascending=False).reset_index(drop=True)
    wr=g[g['wolf']==1].index[0]+1 if (g['wolf']==1).any() else None
    rows.append(dict(event=ev, wolf_theme=g[g['wolf']==1]['theme'].iloc[0] if (g['wolf']==1).any() else '', wolf_rank=wr, top_pred=list(g.head(3)['theme'])))
r=pd.DataFrame(rows)
pd.set_option('display.width',220)
print(r.to_string(index=False))
ranks=r['wolf_rank'].dropna().astype(int)
print()
print('狼大主线排名:', ranks.tolist())
print('top1命中:', round((ranks<=1).mean()*100,0),'%   top3命中:', round((ranks<=3).mean()*100,0),'%  (9主题随机top3≈33%)')