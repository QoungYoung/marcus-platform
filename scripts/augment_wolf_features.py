# -*- coding: utf-8 -*-
"""给特征表补资金蓄积特征(concept主力净流入5/10/20/60d),产出可用数据集 v2。"""
import pandas as pd, numpy as np
MF='data/股票数据/资金流向数据/moneyflow_ind_dc.parquet'
OLD='data/wolf_theme_features.csv'
WOLF=['AI','人工智能','算力','大数据','数据中心','芯片','半导体','CPO','光通信','光模块','存储','内存','PCB','玻璃基板','Chiplet','液冷','服务器','信创','软件','华为','新能源','光伏','储能','锂电','电池','风电','充电桩','电动车','军工','航天','航空','导弹','船舶','无人机','有色','煤炭','石油','化工','稀土','锂','黄金','钢铁','券商','证券','银行','保险','金融','白酒','食品','饮料','家电','消费','零售','医药','创新药','基建','建材','地产','建筑','铁路','水利','水泥']
THEMES={
  'AI/算力/科技':['AI','人工智能','算力','大数据','数据中心','芯片','半导体','CPO','光通信','光模块','存储','内存','PCB','玻璃基板','Chiplet','液冷','服务器','信创','软件','华为'],
  '半导体/芯片':['半导体','芯片','存储','内存','光刻','第三代半导体','第四代半导体'],
  '新能源/电池':['新能源','光伏','储能','锂电','电池','风电','充电桩','电动车','新能源车'],
  '军工/航天':['军工','航天','航空','导弹','船舶','无人机'],
  '资源/周期':['有色','煤炭','石油','化工','稀土','锂','黄金','钢铁'],
  '金融':['券商','证券','银行','保险','金融'],
  '消费/内需':['白酒','食品','饮料','家电','消费','零售','医药','创新药'],
  '医药':['医药','创新药','医疗','生物','CXO','中药'],
  '稳增长/基建':['基建','建材','地产','建筑','铁路','水利','水泥'],
}
SPEC=['涨停','连板','跌停','触板','炸板','首板','打板','高换手','高振幅','昨日','新股','AB股','AH股','融资融券','预盈预增','标普']

def load():
    m=pd.read_parquet(MF); m=m[m['content_type']=='概念']
    m=m[~m['name'].apply(lambda n: any(k in n for k in SPEC))]
    m=m.reset_index(); m.columns=[str(c) for c in m.columns]
    return m

def theme_fund_acc(m, D, theme, wins=(5,10,20,60)):
    D=pd.Timestamp(D)
    names=[n for n in m['name'].unique() if any(k in n for k in THEMES[theme])]
    if not names: return {('fund_boards'):0, **{('fund_acc_%d'%w):np.nan for w in wins}}
    sub=m[m['name'].isin(names)]
    # rows in window ending at D
    sub=sub[sub['trade_date']<=D]
    # sort by date, take last max(wins) rows per board name
    out={k:np.nan for k in [('fund_acc_%d'%w) for w in wins]}
    dn=sub['trade_date'].nunique() if not sub.empty else 0
    for w in wins:
        vals=[]
        for name in names:
            s=sub[sub['name']==name].sort_values('trade_date')
            tail=s.tail(w) if len(s)>=w else s
            if tail.empty: continue
            # cumulative net_amount_rate over window (sum) as 蓄积强度
            vals.append(tail['net_amount_rate'].sum())
        if vals: out['fund_acc_%d'%w]=float(np.mean(vals))
    out['fund_boards']=len(names)
    return out

m=load()
df=pd.read_csv(OLD)
res=[]
for _,r in df.iterrows():
    D=str(r['date']); theme=r['theme']
    fa=theme_fund_acc(m,D,theme)
    row=r.to_dict(); row.update(fa)
    res.append(row)
out=pd.DataFrame(res)
out.to_csv('data/wolf_theme_features_v2.csv',index=False)
pd.set_option('display.width',260); pd.set_option('display.max_columns',None)
print('可用数据集已存 data/wolf_theme_features_v2.csv 行数', len(out))
print('新增列:', [c for c in out.columns if c not in df.columns])
print()
print(out[out['wolf']==1][['date','theme','rs120_ex','lead30_ex','fund_boards','fund_acc_5','fund_acc_10','fund_acc_20','fund_acc_60','vol_exp']].round(3).to_string(index=False))