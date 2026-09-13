# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "/app/app")
from app.services.t_data_sources import fetch_tencent_quote, fetch_minute_bars
q = fetch_tencent_quote(["sh588170"])
print("fetch_tencent_quote sh588170:", (q.get("sh588170") or {}).get("current") if q else None, "| prev_close", (q.get("sh588170") or {}).get("prev_close") if q else None)
b = fetch_minute_bars("sh588170", "m5", 320)
print("fetch_minute_bars sh588170 m5 bars:", len(b) if b else 0, "last", b[-1]["time"] if b else None, "close", b[-1]["close"] if b else None)
