#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P0 探针 ③ — 一交易日日内波动/量能分钟分布（校准 regime 与时段因子）。

目标：
  a) 时段权重：早盘(9:30-10:30)/中盘/尾盘(14:00-15:00) 的波动与量能占比
     → 为"重早盘、避尾盘新增敞口"的时段因子提供实测基准；
  b) 量能分布：日内分钟量曲线形状（U型/倒U型）→ 供盘中量比归一的"同刻基准"参考；
  c) 分钟波动幅度分布：用于 regime L2 阈值初值与可T价差评估。

用法: python probe3_intraday_profile.py
"""
import json
import statistics
from collections import defaultdict
from datetime import datetime
from typing import Dict, List

from data_sources import fetch_tencent_mkline

# 代表性样本（活跃票+权重票各取若干）
SYMBOLS = ["sh600519", "sz300750", "sz002594", "sz300059", "sh600036", "sh601166"]


def bucket_of(time_str: str) -> str:
    """m5 时间戳 '202608141010' → 时段桶: early/mid/late"""
    if len(time_str) < 12:
        return "unknown"
    hm = time_str[8:12]  # HHMM
    if hm <= "1030":
        return "early"
    if hm <= "1130" or "1300" <= hm <= "1400":
        return "mid"
    return "late"


def minute_of(time_str: str) -> int:
    """m5 时间戳 → 当日第几分钟（9:30=0）"""
    hm = time_str[8:12]
    h, m = int(hm[:2]), int(hm[2:])
    if h < 13:
        return (h - 9) * 60 + (m - 30) if h >= 9 else -1
    return (h - 13) * 60 + m + 120  # 午休后继续


def run():
    print("=== P0探针③ 日内波动/量能分钟分布 ===")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    all_buckets = defaultdict(list)   # bucket -> [day_frac]
    all_vol_by_minute = defaultdict(list)  # minute_idx -> [vol_frac]
    per_symbol = {}

    for sym in SYMBOLS:
        bars = fetch_tencent_mkline(sym, "m5", 320)
        if not bars or len(bars) < 40:
            print(f"  {sym}: 数据不足")
            continue
        days: Dict[str, List[dict]] = defaultdict(list)
        for b in bars:
            days[b["time"][:8]].append(b)

        sym_buckets = defaultdict(list)
        sym_vol_min = defaultdict(list)
        for day, db in days.items():
            if len(db) < 20:
                continue
            total_vol = sum(b["vol"] for b in db)
            day_range = max(b["high"] for b in db) - min(b["low"] for b in db)
            # 每根5min bar: 振幅贡献 + 量能占比
            for b in db:
                bkt = bucket_of(b["time"])
                mi = minute_of(b["time"])
                bar_range = b["high"] - b["low"]
                # 波动占比（按bar振幅/日振幅）
                frac = bar_range / day_range if day_range > 0 else 0
                all_buckets[bkt].append(frac)
                sym_buckets[bkt].append(frac)
                # 量能占比
                vfrac = b["vol"] / total_vol if total_vol > 0 else 0
                all_vol_by_minute[mi].append(vfrac)
                sym_vol_min[mi].append(vfrac)

        per_symbol[sym] = {
            "days": len(days),
            "bucket_sum": {k: round(sum(v), 3) for k, v in sym_buckets.items()},
            "bucket_count": {k: len(v) for k, v in sym_buckets.items()},
        }
        print(f"  {sym}: days={len(days)} 时段波动占比 "
              f"{ {k: round(sum(v), 3) for k, v in sym_buckets.items()} }")

    # 汇总
    print("\n=== 时段波动占比（bar振幅/日振幅 之和，均值）===")
    for bkt in ("early", "mid", "late"):
        vals = all_buckets.get(bkt, [])
        if vals:
            print(f"  {bkt}: n={len(vals)} mean={statistics.mean(vals):.4f} "
                  f"total≈{sum(vals):.1f}（m5×6日样本）")

    print("\n=== 分钟量能曲线（vol占比，按当日第几分钟，均值）===")
    # 输出每30分钟一档
    bins = defaultdict(list)
    for mi, vals in all_vol_by_minute.items():
        if mi < 0:
            continue
        bin_key = mi // 30 * 30
        bins[bin_key].extend(vals)
    for bk in sorted(bins):
        vals = bins[bk]
        print(f"  第{bk}-{bk+30}分钟: n={len(vals)} 量能占比均值={statistics.mean(vals):.4f}")

    # 时段量能占比
    print("\n=== 时段量能占比（vol占比均值×根数）===")
    bucket_vol = defaultdict(list)
    for mi, vals in all_vol_by_minute.items():
        bkt = "early" if mi <= 60 else ("mid" if mi <= 180 else "late")
        bucket_vol[bkt].extend(vals)
    for bkt in ("early", "mid", "late"):
        vals = bucket_vol.get(bkt, [])
        if vals:
            print(f"  {bkt}: n={len(vals)} 量能均值={statistics.mean(vals):.4f} "
                  f"累计≈{sum(vals):.2f}")

    with open("output/p0-3-intraday-profile.json", "w", encoding="utf-8") as f:
        json.dump({
            "time": datetime.now().isoformat(),
            "per_symbol": per_symbol,
            "bucket_summary": {k: {"n": len(v), "mean": round(statistics.mean(v), 4)}
                               for k, v in all_buckets.items() if v},
            "note": "样本为近6个交易日m5线；时段桶 early<=10:30, mid 10:30-14:00, late>=14:00",
        }, f, ensure_ascii=False, indent=2)
    print("\n摘要已写 output/p0-3-intraday-profile.json")


if __name__ == "__main__":
    run()
