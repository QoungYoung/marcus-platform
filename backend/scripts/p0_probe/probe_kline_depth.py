#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 probe: Tencent ifzq mkline max depth per freq (how far back can we get)."""
import json
import time
import urllib.request


def fetch(param):
    url = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param=" + param
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read().decode("utf-8")
    dt = time.time() - t0
    d = json.loads(raw)
    code = d.get("code")
    n = d.get("data", {}).get("sh600519", {})
    return code, n, dt


for freq, count in [("m1", 500), ("m5", 1000), ("m15", 1000), ("m30", 1000), ("m60", 1000), ("m60", 2000)]:
    try:
        code, n, dt = fetch(f"sh600519,{freq},,{count}")
        key = freq
        bars = n.get(key, []) if isinstance(n, dict) else []
        if bars:
            print(f"{freq} count={count}: {len(bars)} bars, first={bars[0][0]} last={bars[-1][0]} ({dt:.2f}s)")
        else:
            keys = list(n.keys()) if isinstance(n, dict) else n
            print(f"{freq} count={count}: 0 bars, keys={keys} ({dt:.2f}s)")
    except Exception as e:
        print(f"{freq} count={count}: ERROR {str(e)[:100]}")
    time.sleep(0.4)
