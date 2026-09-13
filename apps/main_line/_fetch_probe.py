# -*- coding: utf-8 -*-
import sys, pandas as pd
sys.path.insert(0,"/app/core")
import tushare as ts
from _api_config import get_tushare_pro
pro=get_tushare_pro()
CSV="/app/data/指数数据/index_daily/000001.SH.csv"
df=pd.read_csv(CSV, parse_dates=["trade_date"]).sort_values("trade_date")
maxd=df["trade_date"].max()
new=pro.index_daily(ts_code="000001.SH", start_date="20260601", end_date="20261231")
new=new[["trade_date","close"]].copy()
new["trade_date"]=pd.to_datetime(new["trade_date"])
new=new[new["trade_date"]>maxd].sort_values("trade_date")
print("existing max:", maxd.date(), "| new rows to append:", len(new), "| new last:", new["trade_date"].iloc[-1].date() if len(new) else None)
if len(new):
    df2=pd.concat([df, new], ignore_index=True).sort_values("trade_date").drop_duplicates("trade_date")
    df2.to_csv(CSV, index=False)
    print("CSV now rows:", len(df2), "| max:", df2["trade_date"].max().date())
else:
    print("no new rows")
