# -*- coding: utf-8 -*-
"""backfill_market_bars_local.py — 本地回填（promax → **本地 SQLite**），避开生产容器与 SSH 隧道写入。

**为什么这样做**（2026-09-13 教训）：
  · 在 marcus-backend 容器里跑重任务会撑爆它 512MB 的 cgroup → 连带 OOM 掉 uvicorn → 生产容器重启（我犯过两次）；
  · 经 SSH 隧道逐行 upsert 到生产 PG 也很慢（隧道吞吐是瓶颈，实测 10 分钟只进 2 天）。
→ 正确架构：**取数与解析在本地、落地到本地 SQLite（快）**；最后用一次 `COPY` 批量灌进生产 PG；
  分析脚本走隧道**只读聚合结果**（每脚本仅拉 ~170×13 行），不构成压力。

用法：python jobs/backfill_market_bars_local.py --start 20241101 --end 20251231 [--db data/mkt_bars_local.db]
"""
import os
import sqlite3
import sys
import time

import requests

DB = os.getenv("LOCAL_BARS_DB", "data/mkt_bars_local.db")
PROMAX_URL = os.getenv("PROMAX_URL", "https://pcd.mobcvb.cn/tushare/pro")
TIMEOUT = float(os.getenv("PROMAX_TIMEOUT", "60"))
ATTEMPTS = int(os.getenv("PROMAX_ATTEMPTS", "5"))

DDL = """
CREATE TABLE IF NOT EXISTS mkt_bars_daily (
    ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, pre_close REAL, pct_chg REAL,
    vol REAL, amount REAL, total_mv REAL, turnover_rate REAL,
    PRIMARY KEY (ts_code, trade_date))
"""
IDX = "CREATE INDEX IF NOT EXISTS idx_lb_date ON mkt_bars_daily (trade_date)"


def _headers():
    k = (os.getenv("PROMAX_API_KEY") or "").strip()
    if not k:
        raise EnvironmentError("PROMAX_API_KEY 未配置")
    return {"X-API-Key": k}


def pm_get(path, **params):
    import urllib3
    urllib3.disable_warnings()
    last = None
    for k in range(ATTEMPTS):
        try:
            r = requests.get(PROMAX_URL + path, params=params or None, headers=_headers(),
                             timeout=TIMEOUT, verify=False)
            if r.status_code in (502, 503, 504):
                raise RuntimeError("HTTP %s" % r.status_code)
            r.raise_for_status()
            j = r.json()
            if j.get("code") not in (0, None):
                raise RuntimeError("code=%s %s" % (j.get("code"), str(j.get("msg"))[:50]))
            return (j.get("data") or {}).get("items") or []
        except Exception as e:
            last = e
            time.sleep(2.0 * (k + 1))
    raise last


def _f(v):
    try:
        return None if v in (None, "", "None") else float(v)
    except (TypeError, ValueError):
        return None


def trade_days(conn, start8, end8):
    items = pm_get("/trade_cal", exchange="SSE", start_date=start8, end_date=end8, is_open="1")
    return sorted(str(x[1]) for x in items if str(x[2]) == "1")


def day_rows(d8):
    items = pm_get("/daily", trade_date=d8)
    out = []
    for it in items:
        if len(it) < 11 or not it[0]:
            continue
        out.append((str(it[0]), d8, _f(it[2]), _f(it[3]), _f(it[4]), _f(it[5]), _f(it[6]),
                    _f(it[8]), _f(it[9]), _f(it[10]), None, None))
    return out


def main():
    a = sys.argv[sys.argv.index("--start") + 1] if "--start" in sys.argv else "20250101"
    b = sys.argv[sys.argv.index("--end") + 1] if "--end" in sys.argv else "20251231"
    db = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else DB
    sleep_sec = float(sys.argv[sys.argv.index("--sleep") + 1]) if "--sleep" in sys.argv else 0.2
    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    conn = sqlite3.connect(db, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(DDL)
    conn.execute(IDX)
    conn.commit()
    have = {r[0] for r in conn.execute(
        "SELECT DISTINCT trade_date FROM mkt_bars_daily WHERE trade_date >= ? AND trade_date <= ?", (a, b))}
    ds = trade_days(conn, a, b)
    todo = [d for d in ds if d not in have]
    print("[lb] %s→%s 交易日 %d，已有 %d，待回填 %d → %s" % (a, b, len(ds), len(have), len(todo), db), flush=True)
    tot, errs = 0, []
    for i, d8 in enumerate(todo, 1):
        try:
            rows = day_rows(d8)
            if not rows:
                print("[lb] %d/%d %s 无数据" % (i, len(todo), d8), flush=True)
                continue
            conn.executemany("INSERT OR REPLACE INTO mkt_bars_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            conn.commit()
            tot += len(rows)
            if i % 5 == 0 or i == len(todo):
                print("[lb] %d/%d %s 写入 %d（累计 %d）" % (i, len(todo), d8, len(rows), tot), flush=True)
        except Exception as e:
            errs.append("%s:%s" % (d8, str(e)[:50]))
            print("[lb] %d/%d %s 失败 %s" % (i, len(todo), d8, str(e)[:60]), flush=True)
        time.sleep(max(0.0, sleep_sec))
    n, d0, d1 = conn.execute("SELECT count(*), min(trade_date), max(trade_date) FROM mkt_bars_daily").fetchone()
    print("[lb] 汇总: 本次写入 %d 行，失败 %d 天 %s | 库内共 %d 行 %s…%s"
          % (tot, len(errs), errs[:3], n, d0, d1), flush=True)
    conn.close()
    return 0 if not errs else 1


if __name__ == "__main__":
    sys.exit(main())
