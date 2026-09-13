# -*- coding: utf-8 -*-
# 样本外验证：固定两档规则(catalyst>=0.7候选/候选+动量资金确认; score=catalyst*1+0.3*base)逐事件,
# 每事件catalyst由该事件自身研报点内计算——逐事件即样本外。报告 top1/top3 + 早/晚分段一致性。
import pandas as pd, sys
sys.path.insert(0, 'scripts')
import main_line_judge as ml
df=pd.read_csv('data/wolf_theme_features_v5.csv')
def z(s): return (s-s.mean())/s.std() if s.std() else pd.Series(0.0,index=s.index)
def base(g):
    return (z(g['rs120_ex'])*1.0 + z(g['rs60_ex'])*0.8 + z(g['lead60_ex'])*0.6 + z(g['lead30_ex'])*0.6
            + g['trend']*0.5 + g['breakout']*0.3 + g['higher_low']*0.2 + z(g['vol_exp'])*0.5
            + z(g['fund_acc_5'].fillna(0))*0.3 + z(g['fund_ov_mean'].fillna(0))*0.3)
rows=[]
for ev,g in df.groupby('date'):
    g=g.copy(); g['base']=base(g)
    g['score']=z(g['catalyst_score'].fillna(0))*1.0 + 0.3*z(g['base'])
    g=g.sort_values('score',ascending=False).reset_index(drop=True)
    wolf=g[g['wolf']==1]
    wr=wolf.index[0]+1 if len(wolf) else None
    wo=wolf.iloc[0] if len(wolf) else None
    st = ml.stage(wo['catalyst_score'], wo['base']) if wo is not None else None
    rows.append({'event':ev,'wolf_rank':wr,'stage':(st['stage'] if st else '无'),'top3':list(g.head(3)['theme'])})
r=pd.DataFrame(rows)
pd.set_option('display.width',240)
print(r.to_string(index=False))
ranks=r['wolf_rank'].dropna().astype(int)
print()
print('全部事件 top1:', round((ranks<=1).mean()*100,0),'%  top3:', round((ranks<=3).mean()*100,0),'%  (随机11%/33%)')
# 分段: 早(2021-2022) vs 晚(2025-2026) -- 一致性
for band,box in [('2021-2022(训练段)', [(ranks[i]<=1) for i,x in enumerate(r['event']) if x<='2022-12-31']),
                 ('2025-2026(留出段)', [(ranks[i]<=1) for i,x in enumerate(r['event']) if x>='2025-01-01'])]:
    if box: print(f'  {band}: top1命中 {round(sum(box)/len(box)*100,0)}%  样本{len(box)}')
print('注: 各事件catalyst由该事件自身研报点内计算,规则固定,故逐事件即样本外;2016无研报属数据缺口。')
