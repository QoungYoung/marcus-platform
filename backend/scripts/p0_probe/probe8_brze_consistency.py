#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 probe: brze tushare vs 腾讯/新浪 数据一致性（rt_min_daily 1min vs 腾讯 m1 vs 新浪 m1）。"""
import statistics
import time
import sys
sys.path.insert(0, r"D:\AIProject\marcus-platform\backend\scripts\p0_probe")
from data_sources import (fetch_brze_rt_min_daily, fetch_brze_stk_mins,
                          fetch_tencent_mkline, fetch_sina_minline)

SYM = "sh600519"
TS_CODE = "600519.SH"

print("=== brze rt_min_daily vs 腾讯 m1 vs 新浪 m1（600519 当日 1min close 对比）===\n")

brze_bars = fetch_brze_rt_min_daily(TS_CODE, "1min")
time.sleep(1)
t_bars = fetch_tencent_mkline(SYM, "m1", 500)
time.sleep(1)
s_bars = fetch_sina_minline(SYM, 1, 300)

print(f"brze 1min: {len(brze_bars) if brze_bars else 0}根")
print(f"腾讯 m1: {len(t_bars) if t_bars else 0}根")
print(f"新浪 m1: {len(s_bars) if s_bars else 0}根")

if brze_bars and t_bars:
    # 归一化时间戳为 202608141500
    b_map = {b["time"].replace("-", "").replace(":", "")[:12]: b["close"] for b in brze_bars}
    t_map = {b["time"][:12]: b["close"] for b in t_bars}
    common = sorted(set(b_map) & set(t_map))
    print(f"\nbrze vs 腾讯 对齐根数: {len(common)}")
    if common:
        diffs = [abs(b_map[k] - t_map[k]) / t_map[k] * 100 for k in common if t_map[k]]
        if diffs:
            print(f"  价差%: mean={statistics.mean(diffs):.4f} max={max(diffs):.4f}")

if brze_bars and s_bars:
    b_map = {b["time"].replace("-", "").replace(":", "")[:12]: b["close"] for b in brze_bars}
    s_map = {b["time"].replace("-", "").replace(":", "")[:12]: b["close"] for b in s_bars}
    common = sorted(set(b_map) & set(s_map))
    print(f"\nbrze vs 新浪 对齐根数: {len(common)}")
    if common:
        diffs = [abs(b_map[k] - s_map[k]) / s_map[k] * 100 for k in common if s_map[k]]
        if diffs:
            print(f"  价差%: mean={statistics.mean(diffs):.4f} max={max(diffs):.4f}")

print("\n=== brze stk_mins 历史60min vs 腾讯 m60 ===")
time.sleep(1)
b60 = fetch_brze_stk_mins(TS_CODE, "60min",
                          start_date="2026-08-10 09:30:00", end_date="2026-08-14 15:00:00")
time.sleep(1)
t60 = fetch_tencent_mkline(SYM, "m60", 320)
if b60 and t60:
    b_map = {b["time"][:13].replace("-", "").replace(":", "")[:12]: b["close"] for b in b60}
    t_map = {b["time"][:12]: b["close"] for b in t60}
    common = sorted(set(b_map) & set(t_map))
    print(f"brze 60min: {len(b60)}根, 腾讯 m60: {len(t60)}根, 对齐: {len(common)}")
    if common:
        diffs = [abs(b_map[k] - t_map[k]) / t_map[k] * 100 for k in common if t_map[k]]
        if diffs:
            print(f"  价差%: mean={statistics.mean(diffs):.4f} max={max(diffs):.4f}")
