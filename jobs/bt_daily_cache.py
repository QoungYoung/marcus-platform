# -*- coding: utf-8 -*-
"""bt_daily_cache.py — 全拟真回测的**日线数据层**：把生产库 `mkt_bars_daily` 落到本地 SQLite，
并提供与生产 `rotation_switch_arm._gz` / `wolf_confirm_pick.gz` **同形状**的取数接口。

## 为什么不让回测直接打 relay
1. 生产选股每天对 80~160 只票各取一次日线（单只 2025-01 起约 0.5~5s）→ 全年重放要几万次外呼，慢且浪费配额；
2. 生产库 `mkt_bars_daily` 已经存了同一份日线（`ts_code/trade_date/open/high/low/close/pre_close/pct_chg/vol/amount/total_mv/turnover_rate`，
   覆盖 2024-11-01→今，5613 标的、413 个交易日）→ 一次导出、本地按 (symbol, 日期区间) 查，快且**口径与生产一致**。

## 字段对齐（关键）
* `_gz('daily', …)` 生产请求字段顺序 = `ts_code,trade_date,close,amount,low,high`（`pick_buy` 用 x[2]=close、x[3]=amount、x[4]=low、x[5]=high）；
  本模块按**调用方给的 fields 串**动态拼元组 → 与 relay 返回完全同形（含 `wolf_confirm_pick.fetch_daily` 的 `ts_code,trade_date,close,low,amount,high`）。
* `daily_basic`（市值预筛）→ 用同一表的 `total_mv`（单位：万元，与 tushare daily_basic.total_mv 同源）。
* **截断**：所有查询强制 `trade_date <= as_of`（回测不许看到未来）。

用法：
  python jobs/bt_daily_cache.py build  --from 20241101 --to 20260915 --db /app/data/_bt_full/bars.sqlite
  python jobs/bt_daily_cache.py check  --db /app/data/_bt_full/bars.sqlite
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time

DB_URL = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
DDL = """
CREATE TABLE IF NOT EXISTS bars (
  ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, pre_close REAL, pct_chg REAL,
  vol REAL, amount REAL, total_mv REAL, turnover_rate REAL,
  PRIMARY KEY (ts_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_bars_date ON bars(trade_date);
"""


def build(db_path: str, d_from: str, d_to: str, chunk_days: int = 40) -> int:
    import psycopg2
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    sq = sqlite3.connect(db_path)
    sq.executescript(DDL)
    pg = psycopg2.connect(DB_URL)
    cur = pg.cursor()
    cur.execute("SELECT DISTINCT trade_date FROM mkt_bars_daily WHERE trade_date BETWEEN %s AND %s ORDER BY 1",
                (d_from, d_to))
    days = [r[0] for r in cur.fetchall()]
    print("[cache] 目标交易日 %d 个（%s → %s）" % (len(days), days[0] if days else '-', days[-1] if days else '-'),
          flush=True)
    total = 0
    for i in range(0, len(days), chunk_days):
        chunk = days[i:i + chunk_days]
        t0 = time.time()
        cur.execute("""SELECT ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount,total_mv,turnover_rate
                       FROM mkt_bars_daily WHERE trade_date BETWEEN %s AND %s""", (chunk[0], chunk[-1]))
        rows = cur.fetchall()
        sq.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        sq.commit()
        total += len(rows)
        print("[cache] %s→%s %d 行（累计 %d，%.1fs）" % (chunk[0], chunk[-1], len(rows), total, time.time() - t0),
              flush=True)
    cur.close(); pg.close(); sq.close()
    print("[cache] 完成：%d 行 → %s" % (total, db_path))
    return total


class Bars:
    """与生产 `_gz` 同形状的本地取数（只读；强制 ≤ as_of）。"""

    def __init__(self, db_path: str, as_of: str = "20991231"):
        self.db = db_path
        self.as_of = str(as_of)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = None
        self.hits = 0

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

    def daily(self, ts_code: str, start_date: str = "19000101", end_date: str = None):
        """→ 元组列表，字段序 = `ts_code,trade_date,close,amount,low,high`（生产 `_gz` 的 daily 形状）。"""
        e = min(str(end_date or self.as_of), self.as_of)
        self.hits += 1
        cur = self._conn.execute(
            "SELECT ts_code,trade_date,close,amount,low,high FROM bars WHERE ts_code=? AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date", (ts_code, str(start_date), e))
        return cur.fetchall()

    def daily_by_fields(self, ts_code: str, fields: str, start_date: str = "19000101", end_date: str = None):
        """按调用方给的 fields 串返回（支持生产用到的任意组合）。"""
        cols = [c.strip() for c in str(fields).split(",") if c.strip()]
        allow = {"ts_code", "trade_date", "open", "high", "low", "close", "pre_close",
                 "pct_chg", "vol", "amount", "total_mv", "turnover_rate"}
        cols = [c for c in cols if c in allow] or ["ts_code", "trade_date", "close"]
        e = min(str(end_date or self.as_of), self.as_of)
        sql = "SELECT %s FROM bars WHERE ts_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date" % ",".join(cols)
        return self._conn.execute(sql, (ts_code, str(start_date), e)).fetchall()

    def daily_basic_mv(self, trade_date: str):
        """→ [(ts_code, total_mv)]（= 市值预筛那一列；与 tushare daily_basic.total_mv 同源）。"""
        d = min(str(trade_date), self.as_of)
        return self._conn.execute("SELECT ts_code,total_mv FROM bars WHERE trade_date=? AND total_mv IS NOT NULL",
                                  (d,)).fetchall()

    def index_like(self, ts_code: str, start_date: str, end_date: str):
        return self.daily_by_fields(ts_code, "ts_code,trade_date,close,open,high,low,vol,amount",
                                    start_date, end_date)


def check(db_path: str) -> int:
    b = Bars(db_path)
    cur = b._conn.execute("SELECT count(*), min(trade_date), max(trade_date), count(DISTINCT ts_code) FROM bars")
    n, d0, d1, ns = cur.fetchone()
    print("[check] %d 行 | %s → %s | %d 标的" % (n, d0, d1, ns))
    for sym in ("002156.SZ", "600584.SH", "512480.SH", "000001.SH"):
        rows = b.daily(sym, "20260901", "20260915")
        print("  %-11s 09-01→09-15 rows=%d %s" % (sym, len(rows), rows[-1] if rows else ''))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "check"])
    ap.add_argument("--db", default=os.path.join(os.environ.get("DATA_DIR", "/app/data"), "_bt_full", "bars.sqlite"))
    ap.add_argument("--from", dest="d_from", default="20241101")
    ap.add_argument("--to", dest="d_to", default="20260915")
    a = ap.parse_args()
    sys.exit(build(a.db, a.d_from, a.d_to) and 0 if a.cmd == "build" else check(a.db))
