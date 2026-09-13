# -*- coding: utf-8 -*-
"""狼大主线事件特征表：对每个事件(D, 狼大主线主题)算各主题的领先特征(点内,含正负样本)。"""
import pandas as pd, numpy as np, json
BASE='data/指数数据'
ci1=pd.read_parquet(BASE+'/ci_l1_daily.parquet').reset_index(); ci1.columns=[str(c) for c in ci1.columns]
close=ci1.pivot_table(index='trade_date',columns='ts_code',values='close')
amt=ci1.pivot_table(index='trade_date',columns='ts_code',values='amount')
name_map=ci1.drop_duplicates('ts_code').set_index('ts_code')['l1_name'].to_dict()
thm=json.load(open('config/theme_map.json',encoding='utf-8'))['theme_map']

# 事件: (日期, 狼大主线主题, 备注)
events=[
  ('2016-01-15','新能源/电池','2016 充电桩/新能源'),
  ('2016-01-26','新能源/电池','2016 新能源主线之一'),
  ('2021-01-03','新能源/电池','2021 机构牛/新能源疯狗浪'),
  ('2022-01-25','新能源/电池','2022 看好锂电/新能源'),
  ('2022-05-06','新能源/电池','2022 锂矿/下一阶段主线'),
  ('2022-06-10','新能源/电池','2022 新能源才是主线'),
  ('2025-01-24','AI/算力/科技','2025 AI机器人数据'),
  ('2025-02-05','AI/算力/科技','2025 固定看好机器人/电池'),
  ('2025-02-06','AI/算力/科技','2025 主线AI机器人'),
  ('2026-01-05','AI/算力/科技','2026 液冷/AIDC'),
  ('2026-02-26','AI/算力/科技','2026 国算/算力'),
]

def ind_feat(code, D, W):
    D=pd.Timestamp(D)
    cs=close[code].dropna(); cs=cs[cs.index<=D]
    if len(cs)<W+1: return None
    c_now=cs.iloc[-1]; c_w=cs.iloc[-W-1]
    rs=c_now/c_w-1
    ma20=cs.iloc[-20:].mean(); ma60=cs.iloc[-60:].mean()
    trend=int(c_now>ma20>ma60)
    prior_high=cs.iloc[-60:-20].max() if len(cs)>=60 else cs.max()
    breakout=int(c_now>=prior_high)
    low_recent=cs.iloc[-20:].min(); low_prior=cs.iloc[-60:-20].min() if len(cs)>=60 else low_recent
    higher_low=int(low_recent>low_prior)
    am=amt[code].dropna(); am=am[am.index<=D]
    vol_exp=(am.iloc[-5:].mean()/am.iloc[-60:].mean()) if len(am)>=60 else np.nan
    return dict(rs=rs, trend=trend, breakout=breakout, higher_low=higher_low, vol_exp=vol_exp)

def theme_feat(theme, D, win_lead):
    inds=[i for i in thm[theme] if i in name_map.values()]
    # map name->code
    code_of={n:c for c,n in name_map.items()}
    feats=[]
    for n in inds:
        c=code_of.get(n)
        if not c: continue
        f=ind_feat(c,D,win_lead)
        if f: feats.append(f)
    if not feats: return None
    df=pd.DataFrame(feats)
    return df.mean()

# 市场基准 = 全行业等权 RS
def mkt_rs(D,W):
    rs=[]
    for code in close.columns:
        f=ind_feat(code,D,W)
        if f and not np.isnan(f['rs']): rs.append(f['rs'])
    return np.mean(rs) if rs else np.nan

rows=[]
for D,wt,label in events:
    D=pd.Timestamp(D)
    # leading RS at D-60/D-30 (over 120/90 windows) and at D (over 60) — use data up to each target
    mkt120=mkt_rs(D,120) if D>=pd.Timestamp('2011-01-01') else np.nan
    mkt60=mkt_rs(D,60)
    for theme in thm:
        f=theme_feat(theme,D,120)
        f60=theme_feat(theme,D,60)
        # leading at D-60 and D-30 (windows ending at those dates)
        f_lead60=theme_feat(theme,D-pd.Timedelta(days=60),120)
        f_lead30=theme_feat(theme,D-pd.Timedelta(days=30),90)
        row=dict(date=str(D.date()), theme=theme, wolf=int(theme==wt), note=label)
        row['rs120']=f['rs'] if f is not None else np.nan; row['rs60']=f60['rs'] if f60 is not None else np.nan
        row['rs120_ex']=f['rs']-mkt120 if f is not None else np.nan; row['rs60_ex']=f60['rs']-mkt60 if f60 is not None else np.nan
        row['lead60_ex']=f_lead60['rs']-mkt_rs(D-pd.Timedelta(days=60),120) if f_lead60 is not None else np.nan
        row['lead30_ex']=f_lead30['rs']-mkt_rs(D-pd.Timedelta(days=30),90) if f_lead30 is not None else np.nan
        row['trend']=f['trend'] if f is not None else np.nan; row['breakout']=f['breakout'] if f is not None else np.nan
        row['higher_low']=f['higher_low'] if f is not None else np.nan; row['vol_exp']=f['vol_exp'] if f is not None else np.nan
        rows.append(row)
df=pd.DataFrame(rows)
df.to_csv('data/wolf_theme_features.csv',index=False)
pd.set_option('display.width',250); pd.set_option('display.max_columns',None)
print('特征表已保存 data/wolf_theme_features.csv, 行数', len(df))
print('列:', list(df.columns))
print(df.head(12).round(3).to_string(index=False))