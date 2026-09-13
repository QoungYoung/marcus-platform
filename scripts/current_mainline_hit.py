# -*- coding: utf-8 -*-
"""滚动【当前主线】命中测试：每周重选一次主线，看是否选中当期/下一段真正领先的主题(允许轮动)。"""
import pandas as pd, numpy as np
MF='data/股票数据/资金流向数据/moneyflow_ind_dc.parquet'
THEMES={
  'AI/算力':['AI','人工智能','算力','大数据','数据中心','芯片','半导体','CPO','光通信','光模块','存储','内存','PCB','玻璃基板','Chiplet','液冷','服务器','信创','软件','华为'],
  '新能源':['新能源','光伏','储能','锂电','电池','风电','充电桩','电动车','新能源车'],
  '军工':['军工','航天','航空','导弹','船舶','无人机'],
  '资源':['有色','煤炭','石油','化工','稀土','锂','黄金','钢铁'],
  '金融':['券商','证券','银行','保险','金融'],
  '消费':['白酒','食品','饮料','家电','消费','零售','医药','创新药'],
  '稳增长':['基建','建材','地产','建筑','铁路','水利','水泥'],
}
SPEC=['涨停','连板','跌停','触板','炸板','首板','打板','高换手','高振幅','昨日','新股','AB股','AH股','融资融券','预盈预增','标普道琼斯']
def load():
    m=pd.read_parquet(MF); m=m[m['content_type']=='概念']
    m=m[~m['name'].apply(lambda n: any(k in n for k in SPEC))]
    m=m.reset_index(); m.columns=[str(c) for c in m.columns]
    return m
def z(s):
    s=pd.to_numeric(s,errors='coerce')
    if s.std()==0 or s.isna().all(): return pd.Series(0.0,index=s.index)
    return (s-s.mean())/s.std()
def main():
    m=load()
    nr=m.pivot_table(index='trade_date',columns='ts_code',values='net_amount_rate')
    pc=m.pivot_table(index='trade_date',columns='ts_code',values='pct_change')
    cl=m.pivot_table(index='trade_date',columns='ts_code',values='close')
    nm=m.drop_duplicates('ts_code').set_index('ts_code')['name'].to_dict()
    # theme member codes
    tm={t:[c for c,n in nm.items() if any(k in n for k in kws)] for t,kws in THEMES.items()}
    # theme daily netrate/pct (equal-weight mean of members) and theme forward return via member close
    dates=sorted(nr.index)
    rows=[]
    for t in THEMES:
        codes=[c for c in tm[t] if c in nr.columns]
        if not codes: continue
        rows.append(dict(theme=t, netrate=nr[codes].mean(axis=1).rename(t), pct=pc[codes].mean(axis=1).rename(t), close=cl[codes].mean(axis=1).rename(t)))
    # build per-theme series
    ndf=pd.concat([r['netrate'] for r in rows],axis=1); pdc=pd.concat([r['pct'] for r in rows],axis=1); cdf=pd.concat([r['close'] for r in rows],axis=1)
    # composite = z(5d mean netrate) + z(5d mean pct), cross-theme per date
    nr5=ndf.rolling(5).mean(); pc5=pdc.rolling(5).mean()
    nr5z=nr5.apply(z); pc5z=pc5.apply(z)
    score=(nr5z+pc5z).dropna()
    # rebalance every 5 trading days from start+some buffer; need 20d forward
    idx=score.index
    H=20
    results=[]
    for pos in range(0, len(idx)-H-1, 5):
        t=idx[pos]
        pred=score.iloc[pos].sort_values(ascending=False)
        pred1=pred.index[0]; pred3=list(pred.index[:3])
        # forward theme return over next H days
        f={}
        for th in cdf.columns:
            csc=cdf[th].dropna(); csc=csc[csc.index>=t]
            if len(csc)>=H+1:
                f[th]=csc.iloc[H]/csc.iloc[0]-1
        fs=pd.Series(f).dropna()
        if fs.empty: continue
        actual_top3=fs.sort_values(ascending=False).head(3).index.tolist()
        results.append(dict(date=t, pred1=pred1, pred3=pred3, actual1=fs.sort_values(ascending=False).index[0], hit1=int(pred1 in actual_top3), hit3=int(any(x in actual_top3 for x in pred3)), fwd=fs.get(pred1, np.nan), fwd_best=fs.max()))
    r=pd.DataFrame(results)
    if r.empty: print('no results'); return
    print('滚动【当前主线】命中测试  (weekly rebalance, 未来'+str(H)+'日领先)')
    print('样本数:', len(r))
    print('hit@top1(选中主线 ∈ 未来3强):', round(r['hit1'].mean()*100,1),'%   (随机基线 3/7≈43%)')
    print('hit@top3(前三任一 ∈ 未来3强):', round(r['hit3'].mean()*100,1),'%')
    print('选中主线的平均未来超额:', round(r['fwd'].mean()*100,1),'%  (真实最强平均', round(r['fwd_best'].mean()*100,1),'%)')
    print()
    print('主线轮动轨迹(每5交易日 选中第1):')
    seq=r[['date','pred1']].copy(); seq['date']=seq['date'].astype(str).str[:10]
    print(seq.to_string(index=False))

if __name__=='__main__': main()