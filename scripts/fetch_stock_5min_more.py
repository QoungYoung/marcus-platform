# -*- coding: utf-8 -*-
"""扩展个股 5min 拉取（brze）：000725/002384/688072 + 校验"""
import json, sys, time
from datetime import date, timedelta
for _p in ("/app", "/app/core", "/app/app"):
    if _p not in sys.path: sys.path.insert(0, _p)
from app.services.t_data_sources import fetch_brze_stk_mins

CODES = ["000725.SZ", "002384.SZ", "688072.SH"]
d0 = date(2025, 12, 1); d1 = date(2026, 9, 1)
dates = []
d = d0
while d <= d1:
    if d.weekday() < 5: dates.append(d.strftime("%Y%m%d"))
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