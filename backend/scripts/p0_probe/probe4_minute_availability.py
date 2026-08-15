#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 探针 ④ — 分钟线可用性实测（腾讯 ifzq + 新浪双源）。

背景：原方案的"分钟线"依赖 Tushare stk_mins/rt_min_daily，但实测 gyzcloud 月卡
到期 403、brze 代理过期、官方 token 无效 → 本探针验证免费替代源（腾讯 ifzq mkline
+ 新浪 minline）能否承担做T所需的分钟级数据：
  a) 各频率成功率/延迟/覆盖率（20只样本）
  b) 历史深度（m1/m5/m15/m30/m60 各能回溯多少）
  c) 指数分钟线（regime L2 用）
  d) 双源交叉一致性（同一标的同频率，腾讯 vs 新浪）

用法: python probe4_minute_availability.py
"""
import json
import statistics
import time
from datetime import datetime
from typing import Dict, List, Optional

from data_sources import fetch_tencent_mkline, fetch_sina_minline

POOL = [
    "sh600519", "sz000858", "sh601318", "sh600036", "sz000001",
    "sh600030", "sz002594", "sh601012", "sz300750", "sh688981",
    "sh600900", "sz002415", "sh601166", "sz000333", "sh600276",
    "sz002475", "sh601888", "sz300059", "sh600887", "sz002304",
]
INDEX_SYMS = ["sh000300", "sh000001", "sz399001"]


def run():
    print("=== P0探针④ 分钟线可用性（腾讯ifzq + 新浪）===")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # a) 腾讯 ifzq：20只 × 各频率
    print("\n--- 腾讯 ifzq mkline 20只 × 频率 ---")
    t_result: Dict[str, dict] = {}
    for freq in ("m1", "m5", "m15", "m60"):
        ok = 0
        lats = []
        depths = []
        for sym in POOL:
            t0 = time.time()
            bars = fetch_tencent_mkline(sym, freq, 320 if freq != "m1" else 500)
            lat = (time.time() - t0) * 1000
            lats.append(lat)
            if bars:
                ok += 1
                depths.append(len(bars))
            time.sleep(0.1)
        rate = ok / len(POOL) * 100
        t_result[freq] = {
            "ok": ok, "total": len(POOL), "rate": round(rate, 1),
            "lat_avg_ms": round(statistics.mean(lats), 0),
            "lat_max_ms": round(max(lats), 0),
            "depth_avg": round(statistics.mean(depths), 0) if depths else 0,
            "depth_max": max(depths) if depths else 0,
        }
        print(f"  {freq}: 成功{ok}/{len(POOL)} ({rate:.0f}%) "
              f"延迟avg={statistics.mean(lats):.0f}ms/max={max(lats):.0f}ms "
              f"深度avg={statistics.mean(depths):.0f}/max={max(depths) if depths else 0}")

    # b) 新浪：20只 × 频率
    print("\n--- 新浪 minline 20只 × 频率 ---")
    s_result: Dict[str, dict] = {}
    for scale in (5, 15, 60):
        ok = 0
        lats = []
        depths = []
        for sym in POOL:
            t0 = time.time()
            bars = fetch_sina_minline(sym, scale, 300)
            lat = (time.time() - t0) * 1000
            lats.append(lat)
            if bars:
                ok += 1
                depths.append(len(bars))
            time.sleep(0.15)
        rate = ok / len(POOL) * 100
        s_result[f"m{scale}"] = {
            "ok": ok, "total": len(POOL), "rate": round(rate, 1),
            "lat_avg_ms": round(statistics.mean(lats), 0),
            "lat_max_ms": round(max(lats), 0),
            "depth_avg": round(statistics.mean(depths), 0) if depths else 0,
        }
        print(f"  m{scale}: 成功{ok}/{len(POOL)} ({rate:.0f}%) "
              f"延迟avg={statistics.mean(lats):.0f}ms 深度avg={statistics.mean(depths):.0f}")

    # c) 指数分钟线（regime L2）
    print("\n--- 指数分钟线（regime L2）---")
    idx_result = {}
    for sym in INDEX_SYMS:
        bars = fetch_sina_minline(sym, 60, 100)
        if bars:
            idx_result[sym] = {"bars": len(bars), "first": bars[0]["time"], "last": bars[-1]["time"]}
            print(f"  {sym}: {len(bars)}根60min {bars[0]['time']}~{bars[-1]['time']}")
        else:
            idx_result[sym] = None
            print(f"  {sym}: FAIL")
        time.sleep(0.3)

    # d) 双源一致性（600519 m5，腾讯vs新浪最近50根 close 相关性）
    print("\n--- 双源一致性（600519 m5 最近50根 close 对齐）---")
    tb = fetch_tencent_mkline("sh600519", "m5", 320)
    sb = fetch_sina_minline("sh600519", 5, 300)
    if tb and sb:
        t_close = {b["time"][:12]: b["close"] for b in tb[-80:]}
        s_close = {b["time"][:12]: b["close"] for b in sb[-80:]}
        common = sorted(set(t_close) & set(s_close))
        if common:
            diff = [abs(t_close[k] - s_close[k]) / t_close[k] * 100 for k in common[-50:]]
            print(f"  对齐根数: {len(common)}  最近50根价差%: "
                  f"mean={statistics.mean(diff):.3f} max={max(diff):.3f}")
        else:
            print("  时间戳未对齐（格式差异）— 需归一化后对比")

    out = {
        "probe": "p0-4-minute-availability",
        "time": datetime.now().isoformat(),
        "tencent": t_result,
        "sina": s_result,
        "index": idx_result,
        "note": "tushare stk_mins 不可用（见报告）；腾讯/新浪为免费替代源",
    }
    with open("output/p0-4-minute-availability.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n摘要已写 output/p0-4-minute-availability.json")


if __name__ == "__main__":
    run()
