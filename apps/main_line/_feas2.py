# -*- coding: utf-8 -*-
import sys
sys.path.insert(0,"/app/core")
import tushare as ts
from _api_config import get_tushare_pro
pro=get_tushare_pro()
def t(name, cb):
    try:
        df=cb()
        if df is None or not len(df): print("[EMPTY]", name); return df
        print("[OK]", name, "rows=",len(df), "cols=",list(df.columns)[:16])
        if len(df): print("   ", df.head(2).to_dict("records"))
        return df
    except Exception as e:
        print("[ERR]", name, str(e)[:140]); return None
# 东财概念/板块接口
t("concept", lambda: pro.concept())
t("concept_detail", lambda: pro.concept_detail(ts_code="BK0493.DC"))
# 同花顺概念/行业 daily
t("ths_daily", lambda: pro.ths_daily(ts_code="885597.TI", start_date="20250101", end_date="20260301"))
t("ths_index", lambda: pro.ths_index(ts_code="885597.TI"))
t("ths_member", lambda: pro.ths_member(ts_code="885597.TI"))
# 同花顺 主题/概念列表
t("ths_daily_kb", lambda: pro.ths_daily(ts_code="8841231.TI", start_date="20250101", end_date="20260301"))
# index_dailybasic 对 BK? 
t("idx_bk_dailybasic", lambda: pro.index_dailybasic(ts_code="BK0493.DC", start_date="20250101", end_date="20260301"))
# moneyflow_ind_dc 概念类型 (content_type=概念?)
t("mf_ind_concept", lambda: pro.moneyflow_ind_dc(trade_date="20260112", content_type="概念"))
