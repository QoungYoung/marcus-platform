#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 probe: brze vs tencent/sina consistency with correct timestamp normalization."""
import statistics
import time
import sys
sys.path.insert(0, r"D:\AIProject\marcus-platform\backend\scripts\p0_probe")
from data_sources import fetch_brze_rt_min_daily, fetch_brze_stk_mins, \
    fetch_tencent_mkline, fetch_sina_minline


def norm_key(t: str) -> str:
    """统一为 12 位 'YYYYMMDDHHMM'（去掉 - : 空格）"""
    return t.replace("-", "").replace(":", "").replace(" ", "")[:12]


print("=== brze rt_min_daily vs 腾讯 m1 vs 新浪 m1（600519 当日 1min close 对比）===\n")
brze = fetch_brze_rt_min_daily("600519.SH", "1min")
time.sleep(1)
t = fetch_tencent_mkline("sh600519", "m1", 500)
time.sleep(1)
s = fetch_sina_minline("sh600519", 1, 300)

b_map = {norm_key(b["time"]): b["close"] for b in (brze or [])}
t_map = {norm_key(b["time"]): b["close"] for b in (t or [])}
s_map = {norm_key(b["time"]): b["close"] for b in (s or [])}

print(f"brze 1min: {len(brze) if brze else 0}根, 腾讯 m1: {len(t) if t else 0}根, 新浪 m1: {len(s) if s else 0}根")

for label, other in [("brze vs 腾讯", t_map), ("brze vs 新浪", s_map)]:
    common = sorted(set(b_map) & set(other))
    print(f"\n{label} 对齐根数: {len(common)}")
    if common:
        diffs = [abs(b_map[k] - other[k]) / other[k] * 100 for k in common if other[k]]
        if diffs:
            print(f"  价差%: mean={statistics.mean(diffs):.4f} max={max(diffs):.4f}")

print("\n=== brze stk_mins 历史60min vs 腾讯 m60 ===")
time.sleep(1)
b60 = fetch_brze_stk_mins("600519.SH", "60min",
                          start_date="2026-08-10 09:30:00", end_date="2026-08-14 15:00:00")
time.sleep(1)
t60 = fetch_tencent_mkline("sh600519", "m60", 320)
if b60 and t60:
    bm = {norm_key(b["time"]): b["close"] for b in b60}
    tm = {norm_key(b["time"]): b["close"] for b in t60}
    common = sorted(set(bm) & set(tm))
    print(f"brze 60min: {len(b60)}根, 腾讯 m60: {len(t60)}根, 对齐: {len(common)}")
    if common:
        diffs = [abs(bm[k] - tm[k]) / tm[k] * 100 for k in common if tm[k]]
        if diffs:
            print(f"  价差%: mean={statistics.mean(diffs):.4f} max={max(diffs):.4f}")
