# -*- coding: utf-8 -*-
"""向前拉长：看狼大确认主线那天 D 之前(D-60/D-30)主题相对强度是否已在积聚。"""
import pandas as pd, numpy as np, json, ast
BASE='data/指数数据'
ci1=pd.read_parquet(BASE+'/ci_l1_daily.parquet').reset_index(); ci1.columns=[str(c) for c in ci1.columns]
close=ci1.pivot_table(index='trade_date',columns='ts_code',values='close')
name_map=ci1.drop_duplicates('ts_code').set_index('ts_code')['l1_name'].to_dict()
thm=json.load(open('config/theme_map.json',encoding='utf-8'))['theme_map']
def theme_rs(t,D,W=120):
    t=pd.Timestamp(t); D=pd.Timestamp(D)
    # use data up to D (point-in-time), window W
    ind={}
    for code in close.columns:
        n=name_map.get(code)
        if not n: continue
        cs=close[code].dropna(); cs=cs[cs.index<=D]
        if len(cs)<W+1: continue
        ind.setdefault(n,[]).append(cs.iloc[-1]/cs.iloc[-W-1]-1)
    if not ind: return np.nan
    ir={n:np.mean(v) for n,v in ind.items()}; mkt=np.mean(list(ir.values()))
    out={}
    for th,inds in thm.items():
        inds=[i for i in inds if i in ir]
        if inds: out[th]=np.mean([ir[i] for i in inds])-mkt
    s=pd.Series(out).sort_values(ascending=False)
    return s

events=[
  ('2016-01-15','新能源/电池','2016 充电桩/新能源'),
  ('2022-06-10','新能源/电池','2022 新能源才是主线'),
  ('2025-02-06','AI/算力/科技','2025 AI/机器人主线'),
  ('2026-02-26','AI/算力/科技','2026 国算/算力主线'),
]
for D,theme,label in events:
    print('='*92)
    print(f'{D}  狼大={theme} ({label})')
    print('='*92)
    for off,wd in [(-60,120),(-30,90),(0,60)]:
        # target date = D+off; window wd (relative strength over wd days ending at that date)
        td=pd.Timestamp(D)+pd.Timedelta(days=off)
        s=theme_rs(td,D,wd) if off>=0 else theme_rs(D,D,wd)  # ensure point-in-time: only use data up to D for all
        # recompute properly: use data up to min(D, target) is D for off<0 too; here we WANT to look before D so use data up to D but window ends at td? Simpler: compute RS up to D for all windows
    # do properly: compute theme rank(RS over window W ending at the actual date td), using only data <= td
    print('  (用截至各时点数据, 主题相对强度排名)')
    for off,wd in [(-60,120),(-30,90),(0,60)]:
        td=pd.Timestamp(D)+pd.Timedelta(days=off)
        s=theme_rs(td,td,wd)  # point-in-time at td
        if isinstance(s,pd.Series) and theme in s.index:
            rk=s.index.get_loc(theme)+1; sc=s[theme]
            flag='✅' if rk<=3 else ('🔶' if rk<=5 else '❌')
            print(f'    D{off:+d} ({td.date()}): {theme} rank={rk}  RS={sc*100:+6.1f}%  {flag}')
        else:
            print(f'    D{off:+d}: 无数据')
print()