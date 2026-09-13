# -*- coding: utf-8 -*-
import sys, json
sys.path.insert(0, "/app/core")
import tushare as ts
from _api_config import get_tushare_pro
pro = get_tushare_pro()
def t(name, cb):
    try:
        df = cb()
        print(f"[OK] {name}: rows={len(df)} cols={list(df.columns)}")
        if len(df): print("   ", df.head(2).to_dict("records"))
    except Exception as e:
        print(f"[ERR] {name}: {str(e)[:160]}")
# ETF 份额/净值/申赎，看有没有
t("fund_share_510300", lambda: pro.fund_share(ts_code="510300.SH"))
t("fund_nav_510300", lambda: pro.fund_nav(ts_code="510300.SH"))
t("fund_daily_510300", lambda: pro.fund_daily(ts_code="510300.SH", start_date="20260601", end_date="20260626"))
t("fund_portfolio", lambda: pro.fund_portfolio(ts_code="510300.SH"))
# ETF 日线行情(成交量=申赎近似)
t("fund_market_510300", lambda: pro.fund_market(ts_code="510300.SH", start_date="20260601", end_date="20260626"))
# 历史覆盖：2016/2021 510300 份额
t("fund_share_510300_2016", lambda: pro.fund_share(ts_code="510300.SH", start_date="20160101", end_date="20160401"))
t("fund_nav_510300_2016", lambda: pro.fund_nav(ts_code="510300.SH", start_date="20160101", end_date="20160401"))
