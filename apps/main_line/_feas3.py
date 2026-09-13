# -*- coding: utf-8 -*-
import sys, pandas as pd
sys.path.insert(0,"/app/core")
import tushare as ts
from _api_config import get_tushare_pro
pro=get_tushare_pro()
# 1) stock_basic: name->ts_code + industry
sb=pro.stock_basic(fields="ts_code,name,industry,market", list_status="L")
print("[OK] stock_basic rows", len(sb), "cols", list(sb.columns))
print("   sample:", sb.head(2).to_dict("records"))
# 2) 概念某日龙头股 → 是否在 stock_basic
mf=pro.moneyflow_ind_dc(trade_date="20260112", content_type="概念")
leaders=mf["buy_sm_amount_stock"].dropna().unique()
print("concept leading names count:", len(leaders))
nm2code=dict(zip(sb["name"], sb["ts_code"]))
hits=[n for n in leaders if n in nm2code]
print("leaders matched to ts_code:", len(hits), "of", len(leaders), "| e.g.", [(n,nm2code[n]) for n in hits[:4]])
# 3) 龙头股 daily vol 能否拉
if hits:
    code=nm2code[hits[0]]
    d=pro.daily(ts_code=code, start_date="20260101", end_date="20260301")
    print("[OK] leader daily", code, "rows", len(d), "cols has vol:", "vol" in d.columns)
