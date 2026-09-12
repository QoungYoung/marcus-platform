# -*- coding: utf-8 -*-
"""research_reports.py — 按日抓全市场研报标题（2026-09-12，catalyst 重建的数据源）。

**为什么这样取**：2026-09-12 实测 promax `research_report` **支持按日期查全市场**
（`trade_date=20260105` → 20 行；2026 上半年抽查 6/8 天有数据，最多一天 154 行），
比 `main_line_judge.py` 现在的"9 主题 × 3 代表股"便宜得多，也更接近狼大"每天读研报定方向"的做法。

**通道特性（实测，必须计入设计）**
  · promax = `https://pcd.mobcvb.cn/tushare/pro`，**GET + X-API-Key**；返回 502/503/504 与连接超时是常态抖动
  · 同一接口不同时刻结果不同（先 504 后 200）→ **必须重试**
  · **取数失败 ≠ 当天没有研报** → 每天预置一行 `pending`，抓取后写 `ok/empty/failed`，
    失败日期可被显式检出并重跑（绝不把 failed 当空值用）

**表**
  · `research_reports_daily(trade_date PK, status, http_status, n_rows, attempts, error, payload jsonb, fetched_at)`
  · `research_reports(trade_date, seq, title, org, author, report_type, ts_code, name, url, PRIMARY KEY(trade_date, seq))`

用法：`python jobs/backfill_research_reports.py --start 20260101 --end 20260531 [--sleep 1.2]`
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence

PROMAX_URL = os.getenv("PROMAX_URL", "https://pcd.mobcvb.cn/tushare/pro")
TIMEOUT = float(os.getenv("PROMAX_TIMEOUT", "12"))
ATTEMPTS = int(os.getenv("PROMAX_ATTEMPTS", "3"))

DDL_DAILY = """
CREATE TABLE IF NOT EXISTS research_reports_daily (
    trade_date  VARCHAR(8) NOT NULL PRIMARY KEY,
    status      VARCHAR(12) NOT NULL DEFAULT 'pending',   -- pending/ok/empty/failed
    http_status INTEGER,
    n_rows      INTEGER DEFAULT 0,
    attempts    INTEGER DEFAULT 0,
    error       TEXT,
    payload     JSONB,
    fetched_at  TIMESTAMPTZ
)
"""
DDL_ROWS = """
CREATE TABLE IF NOT EXISTS research_reports (
    trade_date  VARCHAR(8)  NOT NULL,
    seq         INTEGER     NOT NULL,
    title       TEXT,
    org         TEXT,
    author      TEXT,
    report_type TEXT,
    ts_code     VARCHAR(16),
    name        TEXT,
    url         TEXT,
    PRIMARY KEY (trade_date, seq)
)
"""


def _key() -> str:
    k = os.getenv("PROMAX_API_KEY", "").strip()
    if not k:
        raise EnvironmentError("PROMAX_API_KEY 未配置")
    return k


def _headers() -> Dict[str, str]:
    return {"X-API-Key": _key()}


def fetch_day(d8: str, attempts: Optional[int] = None) -> Dict[str, Any]:
    """抓某日全市场研报；内含重试。返回 {status, http, items, attempts, error}。"""
    import requests
    try:      # 该端点是自签/异常证书，生产一直 verify=False；这里静音噪声警告
        import urllib3
        urllib3.disable_warnings()
    except Exception:
        pass
    n_att = int(attempts or ATTEMPTS)
    last_err = None
    for i in range(1, n_att + 1):
        try:
            r = requests.get(f"{PROMAX_URL}/research_report", params={"trade_date": d8},
                             headers=_headers(), verify=False, timeout=TIMEOUT)
            if r.status_code != 200:
                last_err = "http=%s" % r.status_code
                time.sleep(1.2 * i)
                continue
            j = r.json()
            data = j.get("data") if isinstance(j, dict) else None
            items = (data or {}).get("items") if isinstance(data, dict) else None
            if items is None:
                last_err = "no_items(code=%s)" % (j.get("code") if isinstance(j, dict) else "?")
                time.sleep(1.2 * i)
                continue
            return {"status": "ok" if items else "empty", "http": 200, "items": items,
                    "attempts": i, "error": None}
        except Exception as e:
            last_err = "%s:%s" % (type(e).__name__, str(e)[:60])
            time.sleep(1.2 * i)
    return {"status": "failed", "http": None, "items": [], "attempts": n_att, "error": last_err}


def parse_items(items: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    """items 形如 [trade_date, title, url, report_type, author, name, ts_code, org]（顺序按接口）。"""
    out = []
    for i, it in enumerate(items or []):
        if not isinstance(it, (list, tuple)) or len(it) < 3:
            continue
        g = lambda k: (str(it[k]) if len(it) > k and it[k] is not None else None)  # noqa: E731
        out.append({"seq": i, "trade_date": g(0), "title": g(1), "url": g(2),
                    "report_type": g(3), "author": g(4), "name": g(5), "ts_code": g(6), "org": g(7)})
    return out


def ensure_tables(db) -> None:
    from sqlalchemy import text
    db.execute(text(DDL_DAILY))
    db.execute(text(DDL_ROWS))
    db.commit()


def ensure_days(db, days: Sequence[str]) -> int:
    """为每个交易日预置 pending 行——这样"没抓到"和"当天没有"可以被区分开。"""
    from sqlalchemy import text
    n = 0
    for d8 in days:
        db.execute(text("INSERT INTO research_reports_daily (trade_date) VALUES (:d) "
                        "ON CONFLICT (trade_date) DO NOTHING"), {"d": d8})
        n += 1
    db.commit()
    return n


def upsert_day(db, d8: str, res: Dict[str, Any]) -> int:
    from sqlalchemy import text
    rows = parse_items(res.get("items") or [])
    db.execute(text("DELETE FROM research_reports WHERE trade_date = :d"), {"d": d8})
    for r in rows:
        db.execute(text("""
            INSERT INTO research_reports (trade_date, seq, title, org, author, report_type, ts_code, name, url)
            VALUES (:trade_date, :seq, :title, :org, :author, :report_type, :ts_code, :name, :url)
            ON CONFLICT (trade_date, seq) DO UPDATE
              SET title=EXCLUDED.title, org=EXCLUDED.org, author=EXCLUDED.author,
                  report_type=EXCLUDED.report_type, ts_code=EXCLUDED.ts_code,
                  name=EXCLUDED.name, url=EXCLUDED.url
        """), r)
    db.execute(text("""
        INSERT INTO research_reports_daily (trade_date, status, http_status, n_rows, attempts, error, payload, fetched_at)
        VALUES (:d, :st, :http, :n, :att, :err, CAST(:p AS jsonb), now())
        ON CONFLICT (trade_date) DO UPDATE
          SET status=EXCLUDED.status, http_status=EXCLUDED.http_status, n_rows=EXCLUDED.n_rows,
              attempts=EXCLUDED.attempts, error=EXCLUDED.error, payload=EXCLUDED.payload,
              fetched_at=now()
    """), {"d": d8, "st": res.get("status"), "http": res.get("http"), "n": len(rows),
           "att": res.get("attempts"), "err": res.get("error"),
           "p": json.dumps({"items": res.get("items") or []}, ensure_ascii=False)})
    return len(rows)


def backfill(days: Sequence[str], sleep_sec: float = 1.2, save: bool = True) -> Dict[str, Any]:
    """逐日抓取并入库；返回汇总（含失败日期清单）。"""
    out: Dict[str, Any] = {"ok": True, "days": len(days), "ok_days": 0, "empty_days": 0,
                           "failed_days": [], "rows": 0}
    if not days:
        return {"ok": False, "reason": "no_days"}
    db = None
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        ensure_tables(db)
        ensure_days(db, days)
        for i, d8 in enumerate(days, 1):
            res = fetch_day(d8)
            if save:
                try:
                    n = upsert_day(db, d8, res)
                    db.commit()
                    out["rows"] += n
                except Exception as e:
                    db.rollback()
                    res = {"status": "failed", "http": res.get("http"), "items": [],
                           "attempts": res.get("attempts"), "error": "db:%s" % str(e)[:60]}
                    n = 0
            else:
                n = len(res.get("items") or [])
            if res["status"] == "ok":
                out["ok_days"] += 1
            elif res["status"] == "empty":
                out["empty_days"] += 1
            else:
                out["failed_days"].append(d8)
            print("[rr] %d/%d %s %s 行=%d%s" % (i, len(days), d8, res["status"], n,
                  (" err=%s" % res.get("error")) if res.get("error") else ""), flush=True)
            time.sleep(max(0.0, float(sleep_sec)))
    except Exception as e:
        out["ok"] = False
        out["error"] = str(e)[:100]
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return out


def coverage() -> Dict[str, Any]:
    """覆盖度：按状态统计 + 失败日期清单（回测前先看这个）。"""
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT status, count(*) n, sum(n_rows) rows FROM research_reports_daily GROUP BY 1"
            )).mappings().all()
            failed = [r["trade_date"] for r in db.execute(text(
                "SELECT trade_date FROM research_reports_daily WHERE status='failed' ORDER BY 1"
            )).mappings().all()]
            return {"by_status": [dict(r) for r in rows], "failed_days": failed[:50],
                    "n_failed": len(failed)}
        finally:
            db.close()
    except Exception as e:
        return {"error": str(e)[:80]}
