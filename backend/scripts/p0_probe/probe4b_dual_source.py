#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0探针④b — 双源一致性：腾讯 vs 新浪 m5 close 对齐对比（归一化时间戳）。"""
import statistics
from data_sources import fetch_tencent_mkline, fetch_sina_minline

SYMBOLS = ["sh600519", "sz300750", "sz000001", "sh601166", "sz002594"]


def norm_tencent(t: str) -> str:
    """腾讯 '202608141500' → '2026-08-14 15:00:00'（5min为HHMM）"""
    if len(t) == 12:
        return f"{t[:4]}-{t[4:6]}-{t[6:8]} {t[8:10]}:{t[10:12]}:00"
    return t


def norm_sina(t: str) -> str:
    """新浪 '2026-08-14 15:00:00' 保持"""
    return t


print("=== P0探针④b 双源一致性（腾讯 vs 新浪 m5）===")
for sym in SYMBOLS:
    tb = fetch_tencent_mkline(sym, "m5", 320)
    sb = fetch_sina_minline(sym, 5, 300)
    if not tb or not sb:
        print(f"  {sym}: 数据不足")
        continue
    t_map = {norm_tencent(b["time"]): b["close"] for b in tb}
    s_map = {norm_sina(b["time"]): b["close"] for b in sb}
    common = sorted(set(t_map) & set(s_map))
    if not common:
        print(f"  {sym}: 无对齐时间戳（{len(tb)} vs {len(sb)}）")
        continue
    diffs = []
    for k in common[-50:]:
        if t_map[k]:
            diffs.append(abs(t_map[k] - s_map[k]) / t_map[k] * 100)
    if diffs:
        print(f"  {sym}: 对齐{len(common)}根 最近50根价差% mean={statistics.mean(diffs):.4f} "
              f"max={max(diffs):.4f}（<0.1%即高度一致）")
    else:
        print(f"  {sym}: 对齐但无数值")
