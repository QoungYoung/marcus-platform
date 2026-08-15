#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 探针 ① — 腾讯 qt 30s×20只 成功率/延迟/风控 + 指数实时可取性。

模拟做T监控层的轮询负载：20 只标的按 30s 周期连续轮询 N 轮，
统计每轮成功率、总延迟、单只延迟、是否有被风控（返回空/限流）迹象。
另测指数（沪深300/上证）实时可取性 —— regime L2 日内动态闸门的数据前提。

用法: python probe1_tencent_qt.py [--rounds 20] [--pool 20]
"""
import argparse
import json
import random
import statistics
import time
from datetime import datetime
from typing import List

from data_sources import fetch_tencent_quote

# 做T候选池常用标的（高流动性，模拟真实负载；可换）
POOL = [
    "sh600519", "sz000858", "sh601318", "sh600036", "sz000001",
    "sh600030", "sz002594", "sh601012", "sz300750", "sh688981",
    "sh600900", "sz002415", "sh601166", "sz000333", "sh600276",
    "sz002475", "sh601888", "sz300059", "sh600887", "sz002304",
]
INDEXES = ["sh000300", "sh000001", "sz399001"]


def run(rounds: int = 20, pool: List[str] = None, interval: float = 30.0):
    pool = pool or POOL
    print(f"=== P0探针① 腾讯qt 30s×{len(pool)}只 × {rounds}轮 ===")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    per_round_ok: List[int] = []
    per_round_total_ms: List[float] = []
    per_symbol_ok: dict = {s: 0 for s in pool}
    per_symbol_fail: dict = {s: 0 for s in pool}
    blocked_events = 0
    quote_cache = {}

    for r in range(1, rounds + 1):
        t_start = time.time()
        ok = 0
        # 分批并发（单请求串行模拟，加 jitter；实际监控层用 ThreadPoolExecutor）
        jitter = random.uniform(-3, 3)
        quotes = fetch_tencent_quote(pool)
        round_ms = (time.time() - t_start) * 1000
        for sym, q in quotes.items():
            if q is None:
                per_symbol_fail[sym] += 1
                continue
            ok += 1
            per_symbol_ok[sym] += 1
            if q.get("current", 0) == 0:
                blocked_events += 1  # 非交易时段 current=0 属正常；盘中为0才是异常
        per_round_ok.append(ok)
        per_round_total_ms.append(round_ms)
        if r <= 3 or r == rounds:
            print(f"  轮{r}: 成功{ok}/{len(pool)} 耗时{round_ms:.0f}ms")
        # 模拟 30s 周期（含 jitter）
        sleep = max(0.5, interval + jitter)
        time.sleep(sleep)

    total = rounds * len(pool)
    success_total = sum(per_round_ok)
    print(f"\n=== 结果 ===")
    print(f"总请求: {total}  成功: {success_total}  成功率: {success_total / total * 100:.2f}%")
    print(f"单轮成功率均值: {statistics.mean(per_round_ok) / len(pool) * 100:.2f}%")
    print(f"单轮耗时: min={min(per_round_total_ms):.0f}ms max={max(per_round_total_ms):.0f}ms "
          f"avg={statistics.mean(per_round_total_ms):.0f}ms")
    print(f"current=0 事件(非交易时段正常): {blocked_events}")
    worst = sorted(per_symbol_fail.items(), key=lambda x: -x[1])[:5]
    print(f"失败最多的标的: {worst}")

    # 指数实时
    print(f"\n=== 指数实时（regime L2 数据前提）===")
    idx = fetch_tencent_quote(INDEXES)
    for sym, q in idx.items():
        if q:
            print(f"  {sym} {q['name']}: current={q['current']} change={q['change_pct']}% "
                  f"amount(万)={q['amount']:.0f} elapsed={q['elapsed_s']}s")
        else:
            print(f"  {sym}: FAIL")

    # 结论
    rate = success_total / total * 100
    print(f"\n=== 判定 ===")
    print(f"成功率 {rate:.2f}% {'✅≥98% 达标' if rate >= 98 else '❌<98% 未达标'}")

    # 落盘摘要
    out = {
        "probe": "p0-1-tencent-qt",
        "time": datetime.now().isoformat(),
        "pool_size": len(pool),
        "rounds": rounds,
        "interval_s": interval,
        "total_requests": total,
        "success": success_total,
        "success_rate": round(rate, 2),
        "round_ms": {"min": round(min(per_round_total_ms)), "max": round(max(per_round_total_ms)),
                     "avg": round(statistics.mean(per_round_total_ms))},
        "worst_symbols": worst,
        "indexes": {k: (v.get("current") if v else None) for k, v in idx.items()},
    }
    with open("output/p0-1-tencent-qt.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"摘要已写 output/p0-1-tencent-qt.json")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--interval", type=float, default=30.0)
    args = parser.parse_args()
    run(rounds=args.rounds, interval=args.interval)
