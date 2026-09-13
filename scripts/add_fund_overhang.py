# -*- coding: utf-8 -*-
"""给特征表加公募重仓特征(fund_overhang)：用事件日前一季度 topN 大基金 fund_portfolio 按主题成分股聚合 stk_float_ratio。

2026-09-13: 数据源由 gzcloud 代理改为 datahubco(基础接口)+promax(聚合) 中继（core/tushare_relay.py）。
"""
import json, pandas as pd, numpy as np, ast
from collections import defaultdict


def _relay():
    """加载 core/tushare_relay.py（2026-09-13 起 datahubco 基础接口 + promax 聚合接口，
    替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib, sys
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for _p in pathlib.Path(__file__).resolve().parents:
        if (_p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(_p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def call(api, params, fields=""):
    """Tushare 中继查询（返回 items 行列表；中继内部含重试/分页/双源降级）。"""
    _fields, items = _relay().relay_items(api, fields=fields, **(params or {}))
    return items or []
def quarter_before(date):
    y,m=int(date[:4]),int(date[5:7])
    if m<=3: return f"{y-1}1231"
    if m<=6: return f"{y}0331"
    if m<=9: return f"{y}0630"
    return f"{y}0930"

def toset(c):
    if isinstance(c,(list,tuple,np.ndarray)): return set(c.tolist())
    if isinstance(c,str):
        try: return set(ast.literal_eval(c))
        except: return set()
    return set()

ci1=pd.read_parquet('data/指数数据/ci_l1_daily.parquet')
thm=json.load(open('config/theme_map.json',encoding='utf-8'))['theme_map']
df=pd.read_csv('data/wolf_theme_features_v2.csv')

# 取规模前N大基金
try:
    shares=call('fund_share',{'trade_date':'20260827'},'ts_code,trade_date,fd_share')
except Exception as e:
    print('fund_share err',e); shares=[]
best={}
for code,_d,sh in shares: best[code]=sh
topfunds=[c for c,_ in sorted(best.items(),key=lambda kv:kv[1] or 0,reverse=True)[:10]]
print('大基金数:',len(topfunds))

def fund_overhang(stocks, end_date):
    """聚合这些股票在end_date所在季度被topN大基金持有的 stk_float_ratio 之和。"""
    per_stock=defaultdict(lambda:{'n_funds':0,'sum_float':0.0})
    for f in topfunds:
        try:
            items=call('fund_portfolio',{'ts_code':f,'end_date':end_date},'ts_code,ann_date,end_date,symbol,mkv,amount,stk_mkv_ratio,stk_float_ratio')
        except Exception: continue
        if not items: continue
        # 只取最新披露季度
        maxend=max(it[2] for it in items)
        rows=[it for it in items if it[2]==maxend]
        for r in rows:
            sym=r[3]; fr=r[7] if r[7] is not None else 0.0
            if sym in stocks:
                per_stock[sym]['n_funds']+=1; per_stock[sym]['sum_float']+=fr
    if not per_stock: return None
    vals=[v['sum_float'] for v in per_stock.values()]
    return float(np.mean(vals)), float(np.max(vals)), len(vals)

# 对事件日计算主题overhang
events=df['date'].unique()
overhang_rows=[]
for ev in events:
    y=int(ev[:4])
    if y<2018:  # 无基金持仓数据
        overhang_rows.append((ev,'none')); continue
    end=quarter_before(ev)
    try:
        sub=ci1.xs(pd.Timestamp(ev), level='trade_date')
        ind_stocks={}
        for name,row in zip(sub['l1_name'], sub['con_codes']):
            ind_stocks[name]=toset(row)
    except Exception as e:
        print(ev,'xs err',e); overhang_rows.append((ev,'none')); continue
    for theme,inds in thm.items():
        stocks=set()
        for ind in inds: stocks|=ind_stocks.get(ind,set())
        if not stocks: overhang_rows.append((ev,theme,'none')); continue
        res=fund_overhang(stocks,end)
        overhang_rows.append((ev,theme,'mean',res) if res else (ev,theme,'none'))
    print(f'  {ev} end={end} done')

# 填充到df
lookup={}
for r in overhang_rows:
    if len(r)==2: continue
    if len(r)==3: key=(r[0],r[1]); lookup[key]=(np.nan,np.nan,np.nan)
    else: key=(r[0],r[1]); lookup[key]=r[3]
df['fund_ov_mean']=df.apply(lambda x: lookup.get((str(x['date']),x['theme']),(np.nan,np.nan,np.nan))[0],axis=1)
df['fund_ov_max']=df.apply(lambda x: lookup.get((str(x['date']),x['theme']),(np.nan,np.nan,np.nan))[1],axis=1)
df['fund_ov_nstocks']=df.apply(lambda x: lookup.get((str(x['date']),x['theme']),(np.nan,np.nan,np.nan))[2],axis=1)
df.to_csv('data/wolf_theme_features_v3.csv',index=False)
pd.set_option('display.width',260); pd.set_option('display.max_columns',None)
print('\n已存 data/wolf_theme_features_v3.csv, 新增列 fund_ov_mean/max/nstocks')
print(df[df['wolf']==1][['date','theme','rs120_ex','fund_ov_mean','fund_ov_max','fund_ov_nstocks']].round(3).to_string(index=False))