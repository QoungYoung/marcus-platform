# -*- coding: utf-8 -*-
"""主题持续性检查：狼大定主线那天D,主题须在之后一周/一月持续领先(不轮动掉)才算确认。"""
import pandas as pd, numpy as np
MF='data/股票数据/资金流向数据/moneyflow_ind_dc.parquet'

# 主题 -> 概念关键词(含算力硬件细分), 并排除打板概念
THEMES={
  'AI/算力':['AI','人工智能','算力','大数据','数据中心','芯片','半导体','CPO','光通信','光模块','存储','内存','PCB','玻璃基板','Chiplet','液冷','服务器','信创','软件','华为'],
  '新能源':['新能源','光伏','储能','锂电','电池','风电','充电桩','电动车','新能源车'],
  '军工':['军工','航天','航空','导弹','船舶','无人机'],
  '资源':['有色','煤炭','石油','化工','稀土','锂','黄金','钢铁'],
  '金融':['券商','证券','银行','保险','金融'],
  '消费':['白酒','食品','饮料','家电','消费','零售','医药','创新药'],
  '稳增长':['基建','建材','地产','建筑','铁路','水利','水泥'],
}
SPEC=['涨停','连板','跌停','触板','炸板','首板','打板','高换手','高振幅','昨日','新股']

def load():
    m=pd.read_parquet(MF); m=m[m['content_type']=='概念'].reset_index()
    m.columns=[str(c) for c in m.columns]
    # concept name lookup and exclude spec
    return m

def build(m):
    m=m[~m['name'].apply(lambda n: any(k in n for k in SPEC))]
    nr=m.pivot_table(index='trade_date',columns='ts_code',values='net_amount_rate')
    pc=m.pivot_table(index='trade_date',columns='ts_code',values='pct_change')
    cl=m.pivot_table(index='trade_date',columns='ts_code',values='close')
    nm=m.drop_duplicates('ts_code').set_index('ts_code')['name'].to_dict()
    # map each concept code to themes
    code_theme={code:[] for code in nm}
    for code,name in nm.items():
        for t,kws in THEMES.items():
            if any(k in name for k in kws): code_theme[code].append(t)
    return nr,pc,cl,nm,code_theme

def theme_series(nr,pc,cl,nm,code_theme):
    # per theme daily score = mean over member concepts of z(net_rate)+z(pct) computed daily
    # simpler: daily per-theme net_amount_rate mean and pct mean, then rank by combined over window
    # First build per-theme daily netrate and pct (mean of member codes present)
    theme_nr={}; theme_pc={}
    for t in THEMES:
        codes=[c for c,ts in code_theme.items() if t in ts]
        if not codes: theme_nr[t]=pd.Series(index=nr.index); continue
        theme_nr[t]=nr[codes].mean(axis=1)
        theme_pc[t]=pc[codes].mean(axis=1)
    return theme_nr,theme_pc

def z(s):
    s=pd.to_numeric(s,errors='coerce')
    if s.std()==0 or s.isna().all(): return pd.Series(0.0,index=s.index)
    return (s-s.mean())/s.std()

def main():
    m=load(); nr,pc,cl,nm,ct=build(m)
    theme_nr,theme_pc=theme_series(nr,pc,cl,nm,ct)
    # composite: z of theme netrate + z of theme pct, both averaged over trailing 5d for stability
    tr=pd.DataFrame(theme_nr); tp=pd.DataFrame(theme_pc)
    # trailing 5d mean then combined z-score per date
    nr5=tr.rolling(5).mean(); pc5=tp.rolling(5).mean()
    nr5z=nr5.apply(z); pc5z=pc5.apply(z)
    score=(nr5z+pc5z)
    score=score.dropna()
    D=pd.Timestamp('2026-02-26')
    # window indices
    idx=score.index
    pos=idx.searchsorted(D)
    def rank_at(p):
        row=score.iloc[p]
        return row.sort_values(ascending=False)
    # rank of AI/算力 at D and over next week/month
    # find dates within [D, D+month]
    within=[i for i in range(len(idx)) if idx[i]>=D and idx[i]<=D+pd.Timedelta(days=31)]
    ai='AI/算力'
    rows=[]
    for i in within:
        row=score.iloc[i]
        rk=row.sort_values(ascending=False)
        ai_rank=rk.index.get_loc(ai)+1 if ai in rk.index else np.nan
        rows.append(dict(date=idx[i], ai_rank=ai_rank, ai_score=row[ai] if ai in row else np.nan))
    p=pd.DataFrame(rows)
    print('2026-02-26 起 31天内 AI/算力 主题排名轨迹(概念数据2025-02-26起):')
    for _,r in p.iterrows():
        print('  ', r['date'].date(), 'rank', r['ai_rank'], 'score', round(r['ai_score'],2))
    # persistence metrics
    if not p.empty:
        wk=p[p['date']<=D+pd.Timedelta(days=8)]; mo=p[p['date']>D+pd.Timedelta(days=8)]
        print('\nAI/算力 rank 统计: 当日',p['ai_rank'].iloc[0],'  一周内(',len(wk),')均值',round(wk['ai_rank'].mean(),1),'  一月内(',len(mo),')均值',round(mo['ai_rank'].mean(),1))
        print('AI/算力 排名<=3 的天数占比: 一周',round((wk['ai_rank']<=3).mean()*100,0),'%  一月',round((mo['ai_rank']<=3).mean()*100,0),'%')
        # theme leading each day (top1) count across week/month
        lead_counts={t:0 for t in THEMES}
        for i in within:
            rk=score.iloc[i].sort_values(ascending=False)
            lead_counts[rk.index[0]]=lead_counts.get(rk.index[0],0)+1
        print('\n每日主题第一(31天内):', sorted(lead_counts.items(), key=lambda kv:-kv[1])[:6])

if __name__=='__main__': main()