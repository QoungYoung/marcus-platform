# -*- coding: utf-8 -*-
"""个股 5min 历史拉取（brze 通道，2025-12-01~2026-09-01，断点续拉）
数据: data/stock_5min_{code}.json  {trade_date: [bars]}
"""
import json, sys, time
from datetime import date, timedelta
for _p in ("/app", "/app/core", "/app/app"):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from app.services.t_data_sources import fetch_brze_stk_mins

CODES = ["603259.SH", "603678.SH"]
d0 = date(2025, 12, 1); d1 = date(2026, 9, 1)
dates = []
d = d0
while d <= d1:
    if d.weekday() < 5:
        dates.append(d.strftime("%Y%m%d"))
    d += timedelta(days=1)

for code in CODES:
    out = "/app/data/stock_5min_%s.json" % code[:6]
    try:
        data = json.load(open(out, encoding="utf-8"))
    except Exception:
        data = {}
    todo = [td for td in dates if td not in data]
    print("START", code, "pending", len(todo), flush=True)
    for td in todo:
        try:
            bars = fetch_brze_stk_mins(code, freq="5min", trade_date=td)
            data[td] = bars or []
            print(code, td, len(bars or []), flush=True)
        except Exception as e:
            print(code, td, "ERR", str(e)[:80], flush=True)
            time.sleep(3)
        json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False)
        time.sleep(0.5)
    print("DONE", code, len(data), flush=True)
print("ALL DONE", flush=True)
