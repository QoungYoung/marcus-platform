#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 probe: brze tushare latency (serial, strict spacing)."""
import statistics
import time
import tushare as ts
from tushare.pro import client as _ts_client

_ts_client.DataApi._DataApi__http_url = "https://tu.brze.top"
pro = ts.pro_api('SC9b-_EoiR-gUuR1hHMIddmTqHvF6D_DGOizKGo2KQk')

TS_CODE = "600519.SH"
results = {}


def bench(name, fn, rounds=5):
    lats = []
    for i in range(rounds):
        t0 = time.time()
        try:
            r = fn()
            dt = (time.time() - t0) * 1000
            lats.append(dt if r is not None else None)
            print(f"  {name} #{i+1}: {dt:.0f}ms rows={0 if r is None else len(r)}")
        except Exception as e:
            lats.append(None)
            print(f"  {name} #{i+1}: FAIL {repr(e)[:100]}")
        time.sleep(3)  # brze 文档：单线程 + 间隔
    ok = [x for x in lats if x is not None]
    results[name] = {
        "ok": len(ok), "total": rounds,
        "avg_ms": round(statistics.mean(ok), 1) if ok else None,
    }
    print(f"  => {name}: {len(ok)}/{rounds} avg={results[name]['avg_ms']}ms\n")


print("=== brze tushare 延迟（每源5次串行，间隔3s）===\n")
bench("rt_min 1MIN", lambda: pro.rt_min(ts_code=TS_CODE, freq="1MIN"))
bench("rt_min_daily 1min", lambda: pro.rt_min_daily(ts_code=TS_CODE, freq="1min"))
bench("stk_mins 60min", lambda: pro.stk_mins(
    ts_code=TS_CODE, freq="60min",
    start_date="2026-08-13 09:30:00", end_date="2026-08-14 15:00:00"))
bench("stk_mins 1min trade_date", lambda: pro.stk_mins(
    ts_code=TS_CODE, freq="1min", trade_date="20260814"))
bench("rt_k 600519", lambda: pro.rt_k(ts_code=TS_CODE))
bench("daily 600519", lambda: pro.daily(ts_code=TS_CODE, start_date="20260813", end_date="20260814"))

print("=== 汇总 ===")
for k, v in sorted(results.items(), key=lambda x: x[1]["avg_ms"] or 9999):
    print(f"  {k}: avg={v['avg_ms']}ms ({v['ok']}/{v['total']})")
