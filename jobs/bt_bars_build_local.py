# -*- coding: utf-8 -*-
"""bt_bars_build_local.py — **本地**构建回测用日线缓存（不依赖生产服务器）。

输入（本地文件，一次性同步后即可离线重建）：
  · `data/bars_2026.csv.gz`     —— 全市场 2026 日线（带表头，929k 行，20260105→20260911）
  · `data/_bt_bars_gap.csv.gz`  —— 预热段 + 尾段（**无表头**，20251101→20251231 与 20260912→09-14）
输出：`data/_bt_full/bars.sqlite`（表 `bars`，主键 (ts_code, trade_date)，与 `jobs/bt_daily_cache.py` 同 schema）

用法：
  python jobs/bt_bars_build_local.py [--db data/_bt_full/bars.sqlite]
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__))))
from bt_daily_cache import DDL  # noqa: E402  （复用同一份 schema，避免两套表结构）

COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg",
        "vol", "amount", "total_mv", "turnover_rate"]


def load_csv(sq, path, has_header=True, chunk=20000):
    n = 0
    buf = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        rdr = csv.reader(fh)
        if has_header:
            next(rdr, None)
        for row in rdr:
            if len(row) < 12:
                continue
            buf.append(tuple(row[:12]))
            n += 1
            if len(buf) >= chunk:
                sq.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
                sq.commit()
                buf = []
    if buf:
        sq.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", buf)
        sq.commit()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/_bt_full/bars.sqlite")
    ap.add_argument("--base", default="data/bars_2026.csv.gz", help="带表头的 2026 全市场日线")
    ap.add_argument("--gap", default="data/_bt_bars_gap.csv.gz", help="无表头的预热/尾段")
    a = ap.parse_args()

    os.makedirs(os.path.dirname(os.path.abspath(a.db)), exist_ok=True)
    sq = sqlite3.connect(a.db)
    sq.executescript(DDL)
    total = 0
    for path, hdr in ((a.gap, False), (a.base, True)):
        if not os.path.exists(path):
            print("[bars] 跳过（不存在）:", path)
            continue
        n = load_csv(sq, path, has_header=hdr)
        total += n
        print("[bars] %s → %d 行" % (os.path.basename(path), n), flush=True)
    r = sq.execute("SELECT min(trade_date), max(trade_date), count(*), count(distinct ts_code) FROM bars").fetchone()
    print("[bars] 合计 %d 行（本次写入 %d）| %s → %s | %s 只标的" % (r[2], total, r[0], r[1], r[3]))
    sq.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
