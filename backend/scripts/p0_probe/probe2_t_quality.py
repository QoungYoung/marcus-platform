#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 探针 ② — 可T质量三代理指标 好T vs 差T 标的分隔性。

对一批已知"日内波动特征不同"的样本标的，用腾讯 m5 分钟线（约6个交易日）
计算三个可测代理：
  1) 日内往返度（intraday_turnover）：每分钟线折返次数/日（连续同向≥2根后反向算1次折返）
  2) O-C 回归度（oc_regression）：|收盘-开盘| / 日内振幅，越接近0越适合双向做T
  3) 可T价差空间（t_spread）：日内振幅中位数 − 2×(滑点+手续费)，须 > 0
并输出好/差样本的分布，检验是否可分（为选股阈值提供依据）。

用法: python probe2_t_quality.py
"""
import json
import statistics
from datetime import datetime
from typing import Dict, List, Optional

from data_sources import fetch_tencent_mkline

# 样本：一组高波动活跃票（预期"好T"）+ 一组低波动权重票（预期"差T"）
# 注：样本标签仅作启发式分组，最终以指标分布为准（探针目标正是验证指标是否可分）
ACTIVE = ["sh600519", "sz300750", "sz002594", "sh688981", "sz300059",
          "sz002475", "sh601012", "sz000858", "sh601888", "sz002304"]
STABLE = ["sh600900", "sh601166", "sz000001", "sh600036", "sh601398",
          "sz000333", "sh600028", "sh601988", "sz002415", "sh600276"]

SLIP_TICKS = 0.02   # 双边滑点估算（元，20-60元中价股2-5tick，取中值3tick≈0.03，保守0.02）
FEE_RATE = 0.0006   # 双边手续费+印花估算（万2.5×2 + 印花千1 卖出）


def calc_oc_regression(bars: List[dict]) -> float:
    """O-C 回归度 = |收盘-开盘| / (最高-最低)，0~1，越接近0越往返。"""
    if not bars:
        return 1.0
    day_open = bars[0]["open"]
    day_close = bars[-1]["close"]
    day_high = max(b["high"] for b in bars)
    day_low = min(b["low"] for b in bars)
    rng = day_high - day_low
    if rng <= 0:
        return 1.0
    return abs(day_close - day_open) / rng


def calc_intraday_turnover(bars: List[dict]) -> int:
    """日内往返度：分钟K线中连续同向(≥2根)后反向的次数。

    简化实现：遍历 close 序列，统计"方向切换"次数（>=2根趋势后反向才计数，
    过滤1根抖动）。以 5min 线近似。
    """
    closes = [b["close"] for b in bars]
    if len(closes) < 4:
        return 0
    # 先算每根相对前一根的方向
    dirs = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        if abs(d) < 1e-9:
            dirs.append(0)
        else:
            dirs.append(1 if d > 0 else -1)
    # 压缩连续同向段
    segments = []
    for d in dirs:
        if d == 0:
            continue
        if segments and segments[-1] == d:
            continue
        segments.append(d)
    # 折返次数 = 方向切换次数（相邻段方向相反）
    flips = 0
    for i in range(1, len(segments)):
        if segments[i] != segments[i - 1]:
            flips += 1
    return flips


def calc_t_spread(bars: List[dict]) -> float:
    """可T价差空间 ≈ 日内振幅中位数 − 2×(滑点+手续费)。"""
    if not bars:
        return -1.0
    day_high = max(b["high"] for b in bars)
    day_low = min(b["low"] for b in bars)
    mid_price = (day_high + day_low) / 2 if day_high + day_low > 0 else 1
    amplitude = day_high - day_low
    cost = SLIP_TICKS + amplitude * FEE_RATE  # 单边滑点 + 手续费
    return amplitude - 2 * cost


def analyze_symbol(symbol: str) -> Optional[dict]:
    bars = fetch_tencent_mkline(symbol, "m5", 320)
    if not bars or len(bars) < 40:
        return None
    # 按日分组（m5 时间戳 202608061055 → 取前8位为日期）
    days: Dict[str, List[dict]] = {}
    for b in bars:
        day = b["time"][:8]
        days.setdefault(day, []).append(b)
    day_metrics = []
    for day, db in days.items():
        if len(db) < 20:  # 至少2小时的5min线
            continue
        oc = calc_oc_regression(db)
        trn = calc_intraday_turnover(db)
        sp = calc_t_spread(db)
        day_metrics.append({"day": day, "oc": oc, "turnover": trn, "spread": sp})
    if not day_metrics:
        return None
    return {
        "symbol": symbol,
        "days": len(day_metrics),
        "oc_median": statistics.median(m["oc"] for m in day_metrics),
        "oc_mean": statistics.mean(m["oc"] for m in day_metrics),
        "turnover_median": statistics.median(m["turnover"] for m in day_metrics),
        "turnover_mean": statistics.mean(m["turnover"] for m in day_metrics),
        "spread_median": statistics.median(m["spread"] for m in day_metrics),
        "spread_positive_days": sum(1 for m in day_metrics if m["spread"] > 0),
    }


def run():
    print("=== P0探针② 可T质量三代理 好T vs 差T 分隔性 ===")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("样本: 活跃高波动组(预期好T) vs 权重低波动组(预期差T)，m5×约6交易日\n")

    results = {}
    for group, syms in [("active", ACTIVE), ("stable", STABLE)]:
        print(f"--- {group} ---")
        for s in syms:
            r = analyze_symbol(s)
            if r:
                results[f"{group}:{s}"] = r
                print(f"  {s}: oc={r['oc_median']:.3f} trn={r['turnover_median']} "
                      f"spread={r['spread_median']:.2f} (正价差天数 {r['spread_positive_days']}/{r['days']})")
            else:
                print(f"  {s}: 数据不足")

    # 分组汇总
    print("\n=== 分组汇总 ===")
    for group in ("active", "stable"):
        items = [v for k, v in results.items() if k.startswith(group)]
        if not items:
            continue
        print(f"{group}(n={len(items)}): "
              f"oc_median均值={statistics.mean(i['oc_median'] for i in items):.3f} "
              f"turnover_median均值={statistics.mean(i['turnover_median'] for i in items):.1f} "
              f"spread_median均值={statistics.mean(i['spread_median'] for i in items):.2f}")

    # 简单分隔判定
    act_oc = [v["oc_median"] for k, v in results.items() if k.startswith("active")]
    sta_oc = [v["oc_median"] for k, v in results.items() if k.startswith("stable")]
    act_trn = [v["turnover_median"] for k, v in results.items() if k.startswith("active")]
    sta_trn = [v["turnover_median"] for k, v in results.items() if k.startswith("stable")]
    print("\n=== 分隔判定（均值差/标准差）===")
    if act_oc and sta_oc:
        sep_oc = (statistics.mean(sta_oc) - statistics.mean(act_oc)) / \
                 (statistics.pstdev(act_oc + sta_oc) or 1)
        print(f"O-C回归度: active均值={statistics.mean(act_oc):.3f} stable均值={statistics.mean(sta_oc):.3f} "
              f"分隔度={sep_oc:.2f}σ")
    if act_trn and sta_trn:
        sep_trn = (statistics.mean(act_trn) - statistics.mean(sta_trn)) / \
                  (statistics.pstdev(act_trn + sta_trn) or 1)
        print(f"往返度: active均值={statistics.mean(act_trn):.1f} stable均值={statistics.mean(sta_trn):.1f} "
              f"分隔度={sep_trn:.2f}σ")

    with open("output/p0-2-t-quality.json", "w", encoding="utf-8") as f:
        json.dump({"time": datetime.now().isoformat(), "results": results}, f, ensure_ascii=False, indent=2)
    print("\n摘要已写 output/p0-2-t-quality.json")


if __name__ == "__main__":
    run()
