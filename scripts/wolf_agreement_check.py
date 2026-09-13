# -*- coding: utf-8 -*-
"""和狼大主线一致性检查：在他定主线当天，我们的数据(主题相对强度)能否给出同样结论。

方法论：只用截至该日的 ci_l1 指数(点内)算各主题相对强度(=成员一级行业120/60日累计收益 - 全市场均值)，
排名主题；看狼大标记的主线是否已在 top 序列。不做前向预测，只验证“他定主线那天，数据能不能认出它”。
"""
from __future__ import annotations
import json, ast
import numpy as np
import pandas as pd

BASE="data/指数数据"
def _toset(c):
    if isinstance(c,np.ndarray): return set(c.tolist())
    if isinstance(c,(list,tuple)): return set(c)
    if isinstance(c,str):
        try: return set(ast.literal_eval(c))
        except Exception: return set()
    return set()

def load():
    ci1=pd.read_parquet(f"{BASE}/ci_l1_daily.parquet").reset_index()
    ci1.columns=[str(c) for c in ci1.columns]
    close=ci1.pivot_table(index="trade_date",columns="ts_code",values="close")
    name_map=ci1.drop_duplicates("ts_code").set_index("ts_code")["l1_name"].to_dict()
    thm=json.load(open("config/theme_map.json",encoding="utf-8"))["theme_map"]
    return close, name_map, thm

def industry_ret(close, code, t, window):
    cs=close[code].dropna(); cs=cs[cs.index<=t]
    if len(cs)<window+1: return np.nan
    return cs.iloc[-1]/cs.iloc[-window-1]-1

def theme_rank(close,name_map,thm,t,window,top_n=5):
    t=pd.Timestamp(t)
    # per industry 120d ret
    ret={}
    for code in close.columns:
        name=name_map.get(code)
        # only ci_l1 industries
        r=industry_ret(close,code,t,window)
        if name and not np.isnan(r):
            ret.setdefault(name,[]).append(r)
    ind_ret={n:np.mean(v) for n,v in ret.items()}
    mkt=np.mean(list(ind_ret.values()))
    themes={}
    for th,inds in thm.items():
        inds=[i for i in inds if i in ind_ret]
        if not inds: continue
        themes[th]=np.mean([ind_ret[i] for i in inds]) - mkt
    s=pd.Series(themes).sort_values(ascending=False)
    return s, ind_ret, mkt

def main():
    close,name_map,thm=load()
    events=[
        ("2016-01-15","新能源/电池","2016 充电桩/新能源"),
        ("2016-01-26","新能源/电池","新能源是2016主线之一"),
        ("2021-01-04","消费/内需","2021 机构牛/趋势标(模糊)"),
        ("2022-06-10","新能源/电池","2022 新能源才是主线"),
        ("2025-02-06","AI/算力/科技","2025 AI/机器人主线"),
        ("2026-02-26","AI/算力/科技","2026 国算/算力主线"),
    ]
    for window in [120,60]:
        print("="*86)
        print(f"窗口 = {window} 日相对强度（点内，只用当日及之前数据）")
        print("="*86)
        for date,thm_key,label in events:
            try:
                s,ind,mkt=theme_rank(close,name_map,thm,date,window)
            except Exception as e:
                print(f"{date} {label}: ERR {e}"); continue
            rank=s.index.get_loc(thm_key)+1 if thm_key in s.index else None
            top5=[f"{n}({(v*100):.1f}%)" for n,v in s.head(5).items()]
            col = "✅一致" if (rank is not None and rank<=3) else ("🔶在前列" if (rank is not None and rank<=5) else "❌没认出")
            print(f"{date}  狼大={thm_key}({label})  rank={rank}  市场均值={mkt*100:.1f}%  {col}")
            print(f"        TOP5: {top5}")
        print()

if __name__=="__main__":
    main()
