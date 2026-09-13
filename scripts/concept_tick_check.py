# -*- coding: utf-8 -*-
"""概念级当天一致性检查：2025-02-06 与 2026-02-26 狼大主线对应概念是否排前列。"""
import pandas as pd, numpy as np
MF='data/股票数据/资金流向数据/moneyflow_ind_dc.parquet'

def z(s):
    s=pd.to_numeric(s,errors='coerce')
    if s.std()==0 or s.isna().all(): return pd.Series(0.0,index=s.index)
    return (s-s.mean())/s.std()

def load():
    m=pd.read_parquet(MF)
    m=m[m['content_type']=='概念'].reset_index()
    m.columns=[str(c) for c in m.columns]
    nr=m.pivot_table(index='trade_date',columns='ts_code',values='net_amount_rate')
    pc=m.pivot_table(index='trade_date',columns='ts_code',values='pct_change')
    cl=m.pivot_table(index='trade_date',columns='ts_code',values='close')
    nm=m.drop_duplicates('ts_code').set_index('ts_code')['name'].to_dict()
    return nr,pc,cl,nm

def rank(nr,pc,cl,nm,date,lookback=5,mom=30):
    d=pd.Timestamp(date)
    dates=sorted(nr.index)
    win=[x for x in dates if x<=d][-lookback:]
    if len(win)<2: return None
    nr5=nr.loc[win].mean()
    pc5=pc.loc[win].mean()
    # 30d momentum: close at d / close ~mom days before (use index pos)
    idx=[x for x in dates if x<=d]
    pos=len(idx)-1  # position of d in nr.index (approx, d may be missing -> use last <=d)
    lastdate=idx[-1]
    # find date ~mom trading days earlier in full series
    fullidx=[x for x in dates]
    # position of lastdate
    li=fullidx.index(lastdate)
    target_pos=max(0, li-mom)
    base_date=fullidx[target_pos]
    mom=cl.loc[lastdate]/cl.loc[base_date]-1
    code_index=nr.columns
    df=pd.DataFrame({'code':code_index})
    df['name']=df['code'].map(nm)
    df['netrate']=nr5.reindex(code_index).values
    df['pct']=pc5.reindex(code_index).values
    df['mom']=mom.reindex(code_index).values
    df=df.dropna(subset=['netrate','pct','mom'])
    df['score']=z(df['netrate'])+z(df['pct'])+z(df['mom'])
    return df.sort_values('score',ascending=False).reset_index(drop=True)

events={
  '2025-02-06':('2025 AI/机器人',['AI','人工智能','机器人','算力','数据中心','芯片','半导体','软件','华为','大数据','CPO','光模块','服务器']),
  '2026-02-26':('2026 国算/算力',['算力','数据中心','国产芯片','半导体','AI','人工智能','液冷','服务器','操作系统','信创','软件','华为']),
}

nr,pc,cl,nm=load()
for date,(label,kws) in events.items():
    df=rank(nr,pc,cl,nm,date)
    if df is None:
        print(date,label,'no data'); continue
    print('='*88)
    print(date,label)
    print('='*88)
    def hit(n): return any(k in n for k in kws)
    df['wolf_hit']=df['name'].apply(hit)
    top=df.head(25)
    print('TOP25 概念(score=资金流强度z+5日涨幅z+30日动量z):')
    for i,row in top.iterrows():
        flag='*狼大相关*' if row['wolf_hit'] else ''
        print('  %2d %-16s net_rate=%7.2f%% pct5=%6.2f%% mom30=%6.1f%% score=%6.2f %s'%(
              i+1,row['name'],row['netrate'],row['pct'],row['mom']*100,row['score'],flag))
    wl=df[df['wolf_hit']]
    best=int(df[df['wolf_hit']].index.min())+1 if (not wl.empty) else None
    print('\n狼大相关概念最高排名:',best,' 命中数:',len(wl),' 示例:',wl['name'].head(6).tolist())
    print()