# -*- coding: utf-8 -*-
import sys, pandas as pd
sys.path.insert(0,"/app/core")
import tushare as ts
from _api_config import get_tushare_pro
pro=get_tushare_pro()
def t(name, cb):
    try:
        df=cb(); 
        if df is None or not len(df): print(f"[EMPTY] {name}"); return
        print(f"[OK] {name}: rows={len(df)} cols={list(df.columns)[:14]}")
        if len(df): print("   ", df.head(2).to_dict("records"))
    except Exception as e:
        print(f"[ERR] {name}: {str(e)[:140]}")
# 个股 OHLCV 历史
t("stock_daily_600519", lambda: pro.daily(ts_code="600519.SH", start_date="20250101", end_date="20260301"))
t("stock_dailybasic_600519", lambda: pro.daily_basic(ts_code="600519.SH", start_date="20250101", end_date="20260301"))
# 行业/板块指数：试申万(SI) / 东财(BK) / 指数
t("sw_bank_index", lambda: pro.index_daily(ts_code="801780.SI", start_date="20250101", end_date="20260301"))
t("idx_bk0473", lambda: pro.index_daily(ts_code="BK0473.DC", start_date="20250101", end_date="20260301"))
t("ths_index_bank", lambda: pro.ths_index(ts_code="881121.TI", start_date="20250101", end_date="20260301"))
t("index_member", lambda: pro.index_member(ts_code="000001.SH"))
# 主题成分：wolf_theme_features 有没有成分股
t("moneyflow_ind_dc", lambda: pro.moneyflow_ind_dc(trade_date="20260112"))
