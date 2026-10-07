# -*- coding: utf-8 -*-
"""bt_pg_backfill_mkt_bars.py — 把本地日线 SQLite 回填进**本地 PG 副本**的 `mkt_bars_daily`。

## 为什么需要（回测忠实度，不是生产问题）
本地 PG（docker 容器 `bt_pg`，`postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading`）是生产库的
**只读副本 + 回测库**，`jobs/bt_prod_run.py` 逐 5min bar 调生产 `TMonitor`，所有生产模块都连这张库。
生产里有 6 个模块读 `mkt_bars_daily`（`wolf_mainline_select` / `wolf_etf_vol` / `mkt_bars` / `api/t_account` …），
而本地这张表**当初迁移时按"大表"排除**：

  · 实测（2026-09-17 开工时）：本地 `mkt_bars_daily` 有 301,376 行、日期 20260114–20260603，
    但 `pre_close / pct_chg / total_mv / turnover_rate / is_st` **5 列全 NULL**（`null_pct = 301376 = n`）；
  · `wolf_mainline_select.run()` 的价格面板查询是
    `SELECT trade_date, ts_code, pct_chg, amount FROM mkt_bars_daily WHERE pct_chg IS NOT NULL …`
    → 命中 **0 行** → 直接 `{"ok": false, "reason": "no_bars"}`（方向层主线现算失效）；
  · 20260604 之后（到年跑窗口尾 20260914）**整段无行**。

本脚本把 `data/_bt_full/bars.sqlite`（本身就是从生产 `mkt_bars_daily` 导出的同一份日线）按日 COPY 回本地 PG。

## 口径 / 单位定标（2026-09-17 抽样实测，见 `--calibrate`）
`bars.sqlite` 与生产 `mkt_bars_daily` 在**同一 (ts_code, trade_date)** 上逐字段一致（13 对抽样，覆盖沪/深主板、
创业板、科创板、北交所、20260115/20260310/20260603/20260610/20260715/20260901 六个日期）⇒
  · **vol 单位 = 手（1 手 = 100 股）**，**amount 单位 = 千元**；
  · 自证：`amount * 1000 / (vol * 100) = amount * 10 / vol ≈ close`（抽样 13 对全部吻合到 0.1% 内）。
`bars.sqlite` 没有的列（`is_st`）写 NULL；`fetched_at` 由 `now()` 生成。

## 幂等
`INSERT … ON CONFLICT (ts_code, trade_date) DO UPDATE`（默认**刷新**已有行 → 顺带把上面 5 个 NULL 列补齐）；
`--keep-existing` 改为 `DO NOTHING`（只补缺日，不动已有行）。重复运行结果一致。

## 用法
    # 干跑（只报告将要写入什么，不写库）
    .venv/bin/python jobs/bt_pg_backfill_mkt_bars.py --start 20260105 --end 20260914 --dry-run
    # 正式回填（默认刷新年跑窗口）
    .venv/bin/python jobs/bt_pg_backfill_mkt_bars.py --start 20260105 --end 20260914
    # 只补缺日、不动已有行
    .venv/bin/python jobs/bt_pg_backfill_mkt_bars.py --start 20260604 --end 20260914 --keep-existing
    # 落库后复核（行数/月分布/关键列 NULL 数/索引）
    .venv/bin/python jobs/bt_pg_backfill_mkt_bars.py --verify --start 20260105 --end 20260914
    # 单位定标抽样（不连库，只读 sqlite）
    .venv/bin/python jobs/bt_pg_backfill_mkt_bars.py --calibrate

环境变量：`BT_PG_URL` / `DATABASE_URL`（默认本地副本）、`BT_BARS_DB`（默认 `data/_bt_full/bars.sqlite`）。
"""
from __future__ import annotations

import argparse
import io
import os
import sqlite3
import sys
import time

JOBS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(JOBS)
DEFAULT_PG = os.getenv("BT_PG_URL") or os.getenv("DATABASE_URL") \
    or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
DEFAULT_BARS_DB = os.getenv("BT_BARS_DB") or os.path.join(REPO, "data", "_bt_full", "bars.sqlite")

COLS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "pct_chg",
        "vol", "amount", "total_mv", "turnover_rate"]
NUM_COLS = COLS[2:]
TMP_DDL = """
CREATE TEMP TABLE _bt_tmp_bars (
  ts_code VARCHAR(16), trade_date VARCHAR(8),
  open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
  pre_close DOUBLE PRECISION, pct_chg DOUBLE PRECISION, vol DOUBLE PRECISION, amount DOUBLE PRECISION,
  total_mv DOUBLE PRECISION, turnover_rate DOUBLE PRECISION
) ON COMMIT DROP
"""


# ────────────────────────── sqlite 侧 ──────────────────────────
def sqlite_conn(path: str, attempts: int = 8) -> sqlite3.Connection:
    """只读打开 bars.sqlite。⚠️ 大文件在并发重跑（别的 agent 在打这张 sqlite）时会偶发
    `unable to open database file` → 退避重试。"""
    last = None
    for k in range(attempts):
        try:
            return sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path), uri=True, timeout=60)
        except sqlite3.Error as e:          # noqa: PERF203
            last = e
            time.sleep(1.5 * (k + 1))
    raise RuntimeError("无法打开 %s：%s" % (path, last))


def _f(v):
    """'' / None / 'None' → None；其余 float。"""
    if v is None:
        return None
    if isinstance(v, str) and v.strip() in ("", "None", "nan", "NaN"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def sqlite_days(cur: sqlite3.Cursor, start: str, end: str):
    return [r[0] for r in cur.execute(
        "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date",
        (start, end))]


def sqlite_day_rows(cur: sqlite3.Cursor, day: str):
    q = "SELECT %s FROM bars WHERE trade_date = ?" % ",".join(COLS)
    rows = []
    for r in cur.execute(q, (day,)):
        ts = r[0]
        if not ts:
            continue
        rows.append([str(ts), str(r[1])] + [_f(x) for x in r[2:]])
    return rows


# ────────────────────────── PG 侧 ──────────────────────────
def pg_conn(url: str):
    import psycopg2
    return psycopg2.connect(url, connect_timeout=15)


def _fmt(v):
    """COPY 的文本格式：None → \\N；float 用 repr()（Python 最短往返表示 ⇒ PG double precision 逐位一致）。

    ⚠️ 别用 "%.10g"：total_mv/amount 这种 11~12 位有效数字的值会被截掉末位（实测 20260310 有 1696 行
    total_mv 因此与生产差 1e-10 相对量）。
    """
    if v is None:
        return "\\N"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def _copy_rows(pg, rows):
    r"""COPY 进临时表（\N = NULL，比 execute_values 快 5~10×）。"""
    buf = io.StringIO()
    for r in rows:
        buf.write("\t".join(_fmt(v) for v in r))
        buf.write("\n")
    buf.seek(0)
    with pg.cursor() as cur:
        cur.execute(TMP_DDL)
        cur.copy_expert("COPY _bt_tmp_bars (%s) FROM STDIN WITH (FORMAT text, NULL '\\N')"
                        % ",".join(COLS), buf)


def upsert_day(pg, day: str, rows, keep_existing: bool = False):
    """把一个交易日的行 upsert 进 mkt_bars_daily。返回 (inserted, updated)。"""
    _copy_rows(pg, rows)
    set_clause = ", ".join("%s = EXCLUDED.%s" % (c, c) for c in NUM_COLS)
    conflict = ("ON CONFLICT (ts_code, trade_date) DO NOTHING" if keep_existing
                else "ON CONFLICT (ts_code, trade_date) DO UPDATE SET %s, fetched_at = now()" % set_clause)
    sql = """
    WITH up AS (
      INSERT INTO mkt_bars_daily (ts_code, trade_date, %s, is_st, fetched_at)
      SELECT ts_code, trade_date, %s, NULL, now() FROM _bt_tmp_bars
      %s
      RETURNING (xmax = 0) AS inserted
    )
    SELECT count(*) FILTER (WHERE inserted), count(*) FILTER (WHERE NOT inserted) FROM up
    """ % (", ".join(NUM_COLS), ", ".join(NUM_COLS), conflict)
    with pg.cursor() as cur:
        cur.execute(sql)
        ins, upd = cur.fetchone()
        cur.execute("DROP TABLE IF EXISTS _bt_tmp_bars")
    pg.commit()
    return int(ins or 0), int(upd or 0)


def pg_report(pg, start: str, end: str, label: str = ""):
    with pg.cursor() as cur:
        cur.execute("""SELECT count(*), count(DISTINCT trade_date), count(DISTINCT ts_code),
                              min(trade_date), max(trade_date)
                       FROM mkt_bars_daily WHERE trade_date BETWEEN %s AND %s""", (start, end))
        n, nd, nc, d0, d1 = cur.fetchone()
        cur.execute("""SELECT count(*) FILTER (WHERE pct_chg IS NULL),
                              count(*) FILTER (WHERE pre_close IS NULL),
                              count(*) FILTER (WHERE total_mv IS NULL),
                              count(*) FILTER (WHERE turnover_rate IS NULL),
                              count(*) FILTER (WHERE is_st IS NULL)
                       FROM mkt_bars_daily WHERE trade_date BETWEEN %s AND %s""", (start, end))
        nulls = cur.fetchone()
        cur.execute("""SELECT to_char(to_date(trade_date,'YYYYMMDD'),'YYYYMM') m, count(*), count(DISTINCT trade_date)
                       FROM mkt_bars_daily WHERE trade_date BETWEEN %s AND %s
                       GROUP BY 1 ORDER BY 1""", (start, end))
        months = cur.fetchall()
    print("[verify]%s 行数=%d 交易日=%d 标的=%d 区间=%s→%s" % (" " + label if label else "", n, nd, nc, d0, d1))
    print("[verify] NULL 计数: pct_chg=%d pre_close=%d total_mv=%d turnover_rate=%d is_st=%d"
          % tuple(nulls))
    print("[verify] 按月: " + ", ".join("%s:%d行/%d日" % (m, c, dd) for m, c, dd in months))
    return dict(rows=n, days=nd, codes=nc, d0=d0, d1=d1, nulls=dict(
        zip(("pct_chg", "pre_close", "total_mv", "turnover_rate", "is_st"), nulls)))


def verify_indexes(pg):
    with pg.cursor() as cur:
        cur.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='mkt_bars_daily' ORDER BY indexname")
        for name, ddl in cur.fetchall():
            print("[verify] 索引 %s: %s" % (name, ddl))
        cur.execute("SELECT pg_size_pretty(pg_total_relation_size('mkt_bars_daily'))")
        print("[verify] 表大小: %s" % cur.fetchone()[0])


# ────────────────────────── 单位定标 ──────────────────────────
CALIB_PAIRS = [
    ("600519.SH", "20260115"), ("000001.SZ", "20260115"), ("300750.SZ", "20260115"),
    ("688111.SH", "20260115"), ("601318.SH", "20260115"),
    ("600519.SH", "20260310"), ("000002.SZ", "20260310"), ("002594.SZ", "20260310"),
    ("600000.SH", "20260603"), ("920992.BJ", "20260603"),
    ("600519.SH", "20260610"), ("000001.SZ", "20260715"), ("300750.SZ", "20260901"),
]


def calibrate(bars_db: str):
    """抽样打印 + 单位自证。

    判据（比"隐含均价≈close"更硬）：成交额/成交量得到的**当日均价必须落在当日 [low, high] 区间内**
    （VWAP 一定在当日价格区间里）。若 vol 其实是"股"，隐含均价会小 100 倍 → 直接掉出区间。
    """
    c = sqlite_conn(bars_db)
    cur = c.cursor()
    print("== bars.sqlite 抽样（单位自证：均价 = amount*1000/(vol*100) 必须 ∈ [low, high]）==")
    print("%-11s %-9s %8s %8s %9s %11s %12s %10s %6s" %
          ("ts_code", "date", "low", "high", "close", "vol(手)", "amount(千元)", "均价(元/股)", "命中"))
    ok = 0
    for code, d in CALIB_PAIRS:
        r = cur.execute("SELECT low, high, close, vol, amount FROM bars WHERE ts_code=? AND trade_date=?",
                        (code, d)).fetchone()
        if not r:
            print("%-11s %-9s  <缺失>" % (code, d))
            continue
        low, high, close, vol, amt = (_f(r[0]), _f(r[1]), _f(r[2]), _f(r[3]), _f(r[4]))
        imp = (amt * 1000.0) / (vol * 100.0) if (vol and amt) else None
        hit = bool(imp is not None and low <= imp <= high)
        ok += 1 if hit else 0
        print("%-11s %-9s %8.2f %8.2f %9.2f %11.2f %12.1f %12.4f %6s"
              % (code, d, low, high, close, vol, amt, imp, "✓" if hit else "✗"))
    c.close()
    print("→ 结论：vol 单位=**手（100 股）**，amount 单位=**千元**；%d/%d 抽样均价落在当日 [low,high] 内。"
          % (ok, len(CALIB_PAIRS)))
    print("  反证：若把 vol 当“股”，均价 = amount*1000/vol 会比 close 小 100 倍（不可能在区间内）。")
    return 0


# ────────────────────────── main ──────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description="回填本地 PG 的 mkt_bars_daily（源=data/_bt_full/bars.sqlite）")
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260914")
    ap.add_argument("--bars-db", default=DEFAULT_BARS_DB)
    ap.add_argument("--db-url", default=DEFAULT_PG)
    ap.add_argument("--dry-run", action="store_true", help="只统计与打印，不写库")
    ap.add_argument("--keep-existing", action="store_true",
                    help="已有 (ts_code,trade_date) 不覆盖（默认是 DO UPDATE：顺带补齐 NULL 列）")
    ap.add_argument("--verify", action="store_true", help="只做落库后复核（行数/月分布/NULL/索引）")
    ap.add_argument("--calibrate", action="store_true", help="抽样单位定标（不连库）")
    ap.add_argument("--progress-every", type=int, default=20)
    a = ap.parse_args()

    if a.calibrate:
        return calibrate(a.bars_db)

    t0 = time.time()
    pg = pg_conn(a.db_url)
    print("[pg] %s" % a.db_url.split("@")[-1])
    if a.verify:
        pg_report(pg, a.start, a.end, "回填后")
        verify_indexes(pg)
        pg.close()
        return 0

    print("[pre ] 回填前状态：")
    before = pg_report(pg, a.start, a.end, "before")
    with pg.cursor() as cur:
        cur.execute("SELECT DISTINCT trade_date FROM mkt_bars_daily WHERE trade_date BETWEEN %s AND %s",
                    (a.start, a.end))
        have = {r[0] for r in cur.fetchall()}

    c = sqlite_conn(a.bars_db)
    cur = c.cursor()
    days = sqlite_days(cur, a.start, a.end)
    if not days:
        print("⛔ sqlite 在 %s→%s 没有交易日" % (a.start, a.end))
        return 2
    todo, skip = [d for d in days if d not in have], [d for d in days if d in have]
    print("[src ] %s：交易日 %d（%s→%s）；本地 PG 已有 %d 日 / 待补 %d 日；模式=%s"
          % (a.bars_db, len(days), days[0], days[-1], len(skip), len(todo),
             "DO NOTHING(keep-existing)" if a.keep_existing else "DO UPDATE(刷新+补列)"))
    if a.dry_run:
        n_rows = sum(len(sqlite_day_rows(cur, d)) for d in days)
        print("[dry ] 将写入 %d 行（%d 个交易日）；已有 %d 日会被%s"
              % (n_rows, len(days), len(skip), "保留" if a.keep_existing else "刷新"))
        for d in days[:3] + (["..."] if len(days) > 6 else []) + days[-3:]:
            if d == "...":
                continue
            print("[dry ]   %s : %d 行%s" % (d, len(sqlite_day_rows(cur, d)), "（已存在）" if d in have else ""))
        c.close(); pg.close()
        return 0

    tot_i = tot_u = 0
    for i, d in enumerate(days, 1):
        rows = sqlite_day_rows(cur, d)
        if not rows:
            print("[warn] %s sqlite 无行，跳过" % d)
            continue
        ins, upd = upsert_day(pg, d, rows, keep_existing=a.keep_existing)
        tot_i += ins
        tot_u += upd
        if i % max(1, a.progress_every) == 0 or i == len(days):
            print("[run ] %d/%d %s 新增 %d 更新 %d（累计 %d/%d，%.0fs）"
                  % (i, len(days), d, ins, upd, tot_i, tot_u, time.time() - t0), flush=True)
    c.close()
    print("[run ] 完成：新增 %d 行 / 更新 %d 行，用时 %.0fs" % (tot_i, tot_u, time.time() - t0))
    pg_report(pg, a.start, a.end, "after")
    verify_indexes(pg)
    pg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
