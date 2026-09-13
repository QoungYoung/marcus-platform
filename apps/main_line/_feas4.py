# -*- coding: utf-8 -*-
import sys, pandas as pd
sys.path.insert(0,"/app/core")
import tushare as ts
from _api_config import get_tushare_pro
pro=get_tushare_pro()
def cov(dt):
    try:
        df=pro.moneyflow_ind_dc(trade_date=dt, content_type="概念")
        if df is None or not len(df): print(dt,"EMPTY"); return None
        print(dt,"rows",len(df),"| first concept:", df["name"].iloc[0], df["close"].iloc[0], "| net_amount(亿)", round(df["net_amount"].iloc[0]/1e8,1))
        return set(df["name"])
    except Exception as e:
        print(dt,"ERR",str(e)[:100]); return None
cov("20250815"); cov("20251015"); cov("20251201"); cov("20260212"); cov("20260415"); cov("20260615"); cov("20260828")
# 检查同概念跨日 close 是否有的
d1=cov("20260112"); d2=cov("20260302")
if d1 and d2:
    inter=d1 & d2
    print("concept overlap 0112 vs 0302:", len(inter))
    code="BK0800.DC"
    row1=pro.moneyflow_ind_dc(trade_date="20260112", content_type="概念").query("ts_code==@code")
    row2=pro.moneyflow_ind_dc(trade_date="20260302", content_type="概念").query("ts_code==@code")
    print("AI concept 0112 close:", row1["close"].iloc[0] if len(row1) else None, "| 0302 close:", row2["close"].iloc[0] if len(row2) else None)
