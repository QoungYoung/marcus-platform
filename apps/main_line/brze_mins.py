# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "/app/scripts/p0_probe")
from data_sources import fetch_brze_stk_mins
for dt in ["20260903","20260901"]:
    b = fetch_brze_stk_mins("588170.SH", "5min", trade_date=dt)
    print(dt, "588170.SH bars:", len(b) if b else 0)
# 对照个股(验证brze分钟仍可用)
b2 = fetch_brze_stk_mins("002371.SZ", "5min", trade_date="20260812")
print("002371.SZ 20260812 bars:", len(b2) if b2 else 0, "(对照个股, 应48)")
