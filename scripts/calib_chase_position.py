# -*- coding: utf-8 -*-
"""calib_chase_position.py — 标定「不追高 / 位置」闸的阈值（§9.842）

【为什么 ✓】用户 2026-10-13：
  ·「trend_break_buy 也要过 AI 决策」✓（已做 §9.840）
  ·「深圳华强 … 转正价格比买入还低」「不算追高吗？都快历史新高了吧」✓
    ⇒ 实测该笔买入时点：距 250 日高 **−2.36%**、20 日涨幅 **+51.6%** ✗（贴历史新高 + 急涨 ✓）
  ⇒ 要给「建仓/埋伏类」加**不追高 + 位置**闸 ✓，但阈值**必须先标定** ✗（不擅自定数 ✓）

【做法 ✓】用历史**真实买入成交**（`paper_trades` 的买入行 ✓，全账户 ✓）当作样本 ✓：
  · 入场基准 = **同一复权序列**在成交日的收盘（口径一致 ✓，避免复权价混用 ✗）
  · 特征（as-of ✓）：距 250 日高% ✓、距 20 日高% ✓、20 日涨幅 ✓、5 日涨幅 ✓、当日分位 ✓
  · 结果（前瞻 ✓）：+5/+10/+20 交易日收益（同一序列 ✓）⇒ 中位数 / 胜率 ✓
  · 按【腿型 × 位置分档】给出 n、+10d 中位、胜率 ⇒ 一眼看出"哪一档明显更差"✓

用法：python3 scripts/calib_chase_position.py [--db data/_bt_full/bars_adj.sqlite] [--dsn ...]
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sqlite3
import statistics
import sys

DSN = os.environ.get("BT_PG_DSN", "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")


def load_bars(db: str):
    cn = sqlite3.connect(db)
    cur = cn.cursor()
    cur.execute("select ts_code, trade_date, close, high from bars order by ts_code, trade_date")
    series = collections.defaultdict(lambda: {"d": [], "c": [], "h": []})
    for ts, d, c, h in cur:
        try:
            c = float(c)
            h = float(h or c)
        except Exception:
            continue
        s = series[ts]
        s["d"].append(str(d).replace("-", ""))
        s["c"].append(c)
        s["h"].append(h)
    cn.close()
    return series


def feats(s, d8):
    """返回 (idx, 距250日高%, 距20日高%, 20日涨幅%, 5日涨幅%)"""
    i = None
    # 二分不必：样本不大，直接倒找
    for k in range(len(s["d"]) - 1, -1, -1):
        if s["d"][k] <= d8:
            i = k
            break
    if i is None or i < 21:
        return None
    c = s["c"][i]
    hi250 = max(s["h"][max(0, i - 249):i + 1])
    hi20 = max(s["h"][max(0, i - 19):i + 1])
    g20 = (c / s["c"][i - 20] - 1) * 100 if s["c"][i - 20] else 0
    g5 = (c / s["c"][i - 5] - 1) * 100 if s["c"][i - 5] else 0
    return i, (c / hi250 - 1) * 100 if hi250 else 0, (c / hi20 - 1) * 100 if hi20 else 0, g20, g5


def fwd(s, i, n):
    j = i + n
    if j >= len(s["c"]) or not s["c"][i]:
        return None
    return (s["c"][j] / s["c"][i] - 1) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/_bt_full/bars_adj.sqlite")
    ap.add_argument("--dsn", default=DSN)
    args = ap.parse_args()
    import psycopg2
    cn = psycopg2.connect(args.dsn)
    cur = cn.cursor()
    cur.execute("""select symbol, trade_date::text, price, coalesce(reason,''), account_id
                   from paper_trades
                   where direction like '买%' and coalesce(voided,0)=0
                   order by trade_date""")
    rows = cur.fetchall()
    cn.close()
    print("  买入成交样本 = %d 笔 ✓" % len(rows))
    bars = load_bars(args.db)
    recs = []
    for sym, tdate, price, reason, acct in rows:
        ts = None
        s0 = str(sym or "").upper()
        m = re.match(r"^(SH|SZ|BJ)(\d{6})$", s0)
        if m:
            ts = "%s.%s" % (m.group(2), m.group(1))
        elif re.match(r"^\d{6}\.(SH|SZ|BJ)$", s0):
            ts = s0
        if not ts or ts not in bars:
            continue
        d8 = str(tdate).replace("-", "")[:8]
        f = feats(bars[ts], d8)
        if not f:
            continue
        i, d250, d20, g20, g5 = f
        kind = ""
        mm = re.search(r"（([a-z0-9_]+)）", reason or "")
        if mm:
            kind = mm.group(1)
        elif "埋伏" in (reason or "") or "转正" in (reason or ""):
            kind = "ambush_add"
        recs.append({"sym": ts, "d": d8, "kind": kind or "(未标)", "d250": d250, "d20": d20,
                     "g20": g20, "g5": g5, "f5": fwd(bars[ts], i, 5), "f10": fwd(bars[ts], i, 10),
                     "f20": fwd(bars[ts], i, 20)})
    print("  可用样本（有复权序列 ✓）= %d 笔\n" % len(recs))
    bins = [(-999, -5), (-5, -2), (-2, 0), (0, 999)]
    print("  【按「距 250 日高」分档 ✓】（入场在 20 日高之上/之下也一并看 ✓）")
    print("    %-16s %6s %10s %8s %10s %8s" % ("档位", "n", "+10d中位%", "胜率%", "+20d中位%", "胜率%"))
    for lo, hi in bins:
        sub = [r for r in recs if lo <= r["d250"] < hi and r["f10"] is not None]
        if not sub:
            continue
        med = statistics.median([r["f10"] for r in sub])
        win = 100.0 * sum(1 for r in sub if r["f10"] > 0) / len(sub)
        sub20 = [r for r in sub if r["f20"] is not None]
        med20 = statistics.median([r["f20"] for r in sub20]) if sub20 else float("nan")
        win20 = (100.0 * sum(1 for r in sub20 if r["f20"] > 0) / len(sub20)) if sub20 else float("nan")
        print("    距高 %-10s %6d %10.2f %8.1f %10.2f %8.1f" % ("%.0f~%.0f%%" % (lo, hi), len(sub), med, win, med20, win20))
    print("\n  【按「20 日涨幅」分档 ✓】")
    print("    %-16s %6s %10s %8s" % ("档位", "n", "+10d中位%", "胜率%"))
    for lo, hi in [(-999, 0), (0, 15), (15, 30), (30, 999)]:
        sub = [r for r in recs if lo <= r["g20"] < hi and r["f10"] is not None]
        if not sub:
            continue
        med = statistics.median([r["f10"] for r in sub])
        win = 100.0 * sum(1 for r in sub if r["f10"] > 0) / len(sub)
        print("    涨幅 %-12s %6d %10.2f %8.1f" % ("%.0f~%.0f%%" % (lo, hi), len(sub), med, win))
    print("\n  【腿型 × 距高】交叉（n≥5 才显示 ✓）")
    for kind in sorted({r["kind"] for r in recs}):
        for lo, hi in bins:
            sub = [r for r in recs if r["kind"] == kind and lo <= r["d250"] < hi and r["f10"] is not None]
            if len(sub) < 5:
                continue
            med = statistics.median([r["f10"] for r in sub])
            win = 100.0 * sum(1 for r in sub if r["f10"] > 0) / len(sub)
            print("    %-18s 距高 %-12s n=%-4d +10d中位=%7.2f 胜率=%5.1f%%" % (kind, "%.0f~%.0f%%" % (lo, hi), len(sub), med, win))


if __name__ == "__main__":
    main()
