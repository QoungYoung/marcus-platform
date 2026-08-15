#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 probe: 实时性对比 — 免费接口(腾讯qt/ifzq/新浪) vs brze tushare(rt_min/rt_min_daily/stk_mins)。
每源单线程串行测 5 次取延迟，严格间隔避免限流。"""
import statistics
import time
import tushare as ts
from tushare.pro import client as _ts_client

_ts_client.DataApi._DataApi__http_url = "https://tu.brze.top"
pro = ts.pro_api('SC9b-_EoiR-gUuR1hHMIddmTqHvF6D_DGOizKGo2KQk')

import sys, os
sys.path.insert(0, r"D:\AIProject\marcus-platform\backend\scripts\p0_probe")
from data_sources import fetch_tencent_quote, fetch_tencent_mkline, fetch_sina_minline

SYM = "sh600519"  # 腾讯格式
TS_CODE = "600519.SH"  # tushare 格式

results = {}


def bench(name, fn, rounds=5):
    lats = []
    for i in range(rounds):
        t0 = time.time()
        try:
            r = fn()
            dt = (time.time() - t0) * 1000
            lats.append(dt if r is not None else None)
        except Exception:
            lats.append(None)
        time.sleep(1.5)  # 严格串行间隔
    ok = [x for x in lats if x is not None]
    results[name] = {
        "ok": len(ok), "total": rounds,
        "avg_ms": round(statistics.mean(ok), 1) if ok else None,
        "min_ms": round(min(ok), 1) if ok else None,
        "max_ms": round(max(ok), 1) if ok else None,
    }
    print(f"{name}: {len(ok)}/{rounds} 成功 avg={results[name]['avg_ms']}ms "
          f"min={results[name]['min_ms']} max={results[name]['max_ms']}")


print("=== 实时性对比（每源5次串行，间隔1.5s）===\n")

# 免费源
bench("腾讯qt 实时行情(1只)", lambda: fetch_tencent_quote([SYM]))
bench("腾讯ifzq m1 分钟线(500根)", lambda: fetch_tencent_mkline(SYM, "m1", 500))
bench("新浪 m1 分钟线(300根)", lambda: fetch_sina_minline(SYM, 1, 300))

# brze tushare
bench("brze rt_min 实时分钟(1MIN)", lambda: pro.rt_min(ts_code=TS_CODE, freq="1MIN"))
bench("brze rt_min_daily 当日分钟(1min)", lambda: pro.rt_min_daily(ts_code=TS_CODE, freq="1min"))
bench("brze stk_mins 历史分钟(60min)", lambda: pro.stk_mins(
    ts_code=TS_CODE, freq="60min",
    start_date="2026-08-13 09:30:00", end_date="2026-08-14 15:00:00"))
bench("brze rt_k 实时日线", lambda: pro.rt_k(ts_code=TS_CODE))

print("\n=== 汇总 ===")
for k, v in sorted(results.items(), key=lambda x: x[1]["avg_ms"] or 9999):
    print(f"  {k}: avg={v['avg_ms']}ms ({v['ok']}/{v['total']})")
