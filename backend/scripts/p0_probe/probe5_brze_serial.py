#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 probe: brze final serial test (per vendor doc: 单线程串行 + 间隔 + 重试).
Token: SC9b-_EoiR-gUuR1hHMIddmTqHvF6D_DGOizKGo2KQk（代码库正确 token；C9b 无 S 为 invalid）
严格间隔 5s，失败 sleep 3s 重试 2 次，绝不并发。
"""
import time
import tushare as ts
from tushare.pro import client as _ts_client

_ts_client.DataApi._DataApi__http_url = "https://tu.brze.top"
pro = ts.pro_api('SC9b-_EoiR-gUuR1hHMIddmTqHvF6D_DGOizKGo2KQk')


def test(name, fn):
    for attempt in range(3):
        try:
            df = fn()
            n = 0 if df is None else len(df)
            print(f"[OK] {name} rows={n}")
            if n > 0:
                print(df.head(2).to_string())
            return True
        except Exception as e:
            print(f"[RETRY] {name} attempt{attempt+1}: {repr(e)[:140]}")
            time.sleep(5)  # 失败后等待（文档建议1-3s，用5s更稳）
    print(f"[FAIL] {name}")
    return False


# 按文档顺序串行，每个之间间隔 5s
test("user_info", lambda: pro.user_info())
time.sleep(5)
test("stk_mins 60min start/end", lambda: pro.stk_mins(
    ts_code="600519.SH", freq="60min",
    start_date="2026-08-13 09:30:00", end_date="2026-08-14 15:00:00"))
time.sleep(5)
test("stk_mins 1min trade_date", lambda: pro.stk_mins(
    ts_code="600519.SH", freq="1min", trade_date="20260814"))
time.sleep(5)
test("rt_k 600519", lambda: pro.rt_k(ts_code="600519.SH"))
time.sleep(5)
test("rt_min 1MIN", lambda: pro.rt_min(ts_code="600519.SH", freq="1MIN"))
time.sleep(5)
test("rt_min_daily 1min", lambda: pro.rt_min_daily(ts_code="600519.SH", freq="1min"))
print("=== DONE ===")
