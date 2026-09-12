# -*- coding: utf-8 -*-
"""mkt_bars.py — 回测用行情基础表（2026-09-12）。

**为什么**：真实回测需要"按日、按票"的行情底座，而生产里现成的 `t_vreb_daily` 只覆盖
2026-05-13 → 09-01（且无 pre_close / pct_chg / amount / total_mv），ETF 日线只有 14 天。
本模块从 **tushare 直接拉**（用户口径：行情/基础数据走 tushare），落一张干净的表：

    mkt_bars_daily(ts_code, trade_date, open, high, low, close, pre_close, pct_chg,
                   vol, amount, total_mv, turnover_rate, is_st, fetched_at)
    主键 (ts_code, trade_date)；trade_date 上建索引供按日扫描。

数据源与字段
  · `pro.daily(trade_date=d)`        → open/high/low/close/pre_close/pct_chg/vol/amount
  · `pro.daily_basic(trade_date=d)`  → total_mv/turnover_rate（失败不阻断，留空）
  · `pro.stock_basic()`              → name（含 ST/*ST 判 is_st），启动取一次并缓存
  · `pro.trade_cal(exchange='SSE')`  → 交易日历（不依赖 run 时其它日历来历）

用法（生产容器）：
  python jobs/backfill_market_bars.py --start 20260101 --end 20260911[ --sleep 0.4]
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Sequence

BATCH = 2000


def _pro():
    from app.core.trading._api_config import get_tushare_pro
    return get_tushare_pro()


def trade_days(start8: str, end8: str) -> List[str]:
    """交易日历（SSE，仅开市）；失败时退回 resolve_trade_days。"""
    try:
        pro = _pro()
        df = pro.trade_cal(exchange="SSE", start_date=start8, end_date=end8, is_open="1")
        days = sorted({str(x) for x in df["cal_date"].tolist()})
        if days:
            return days
    except Exception as e:
        print(f"[mkt_bars] trade_cal 失败: {type(e).__name__}: {str(e)[:70]}")
    try:
        from app.services.t_backtest_data import resolve_trade_days
        return sorted({str(d)[:8] for d in (resolve_trade_days(start8, end8) or []) if str(d)[:8].isdigit()})
    except Exception:
        return []


def st_map() -> Dict[str, bool]:
    """{ts_code: 是否ST}（按名称含 ST 判定；失败返回空 → is_st 留空）。"""
    out: Dict[str, bool] = {}
    try:
        pro = _pro()
        df = pro.stock_basic(exchange="", list_status="L", fields="ts_code,name")
        for ts, nm in zip([str(x) for x in df["ts_code"]], [str(x) for x in df["name"]]):
            out[ts] = ("ST" in nm.upper())
    except Exception as e:
        print(f"[mkt_bars] stock_basic 失败: {type(e).__name__}: {str(e)[:70]}")
    return out


def day_rows(d8: str, sts: Optional[Dict[str, bool]] = None) -> List[Dict[str, Any]]:
    """取某日全市场日线（+市值/换手），返回可入库的行列表。"""
    pro = _pro()
    df = pro.daily(trade_date=d8)
    if df is None or len(df) == 0:
        return []
    basics: Dict[str, Any] = {}
    try:
        b = pro.daily_basic(trade_date=d8, fields="ts_code,total_mv,turnover_rate")
        if b is not None and len(b):
            for ts, mv, tr in zip([str(x) for x in b["ts_code"]], b["total_mv"], b["turnover_rate"]):
                basics[ts] = (mv, tr)
    except Exception as e:
        print(f"[mkt_bars] daily_basic({d8}) 失败（留空）: {type(e).__name__}: {str(e)[:50]}")
    rows: List[Dict[str, Any]] = []
    for r in df.itertuples(index=False):
        ts = str(getattr(r, "ts_code"))
        mv, tr = basics.get(ts, (None, None))
        rows.append({
            "ts_code": ts, "trade_date": d8,
            "open": _f(getattr(r, "open", None)), "high": _f(getattr(r, "high", None)),
            "low": _f(getattr(r, "low", None)), "close": _f(getattr(r, "close", None)),
            "pre_close": _f(getattr(r, "pre_close", None)), "pct_chg": _f(getattr(r, "pct_chg", None)),
            "vol": _f(getattr(r, "vol", None)), "amount": _f(getattr(r, "amount", None)),
            "total_mv": _f(mv), "turnover_rate": _f(tr),
            "is_st": (None if not sts else bool(sts.get(ts, False))),
        })
    return rows


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None      # 过滤 NaN
    except (TypeError, ValueError):
        return None


def ensure_table(db) -> None:
    from sqlalchemy import text
    db.execute(text("""
        CREATE TABLE IF NOT EXISTS mkt_bars_daily (
            ts_code       VARCHAR(16) NOT NULL,
            trade_date    VARCHAR(8)  NOT NULL,
            open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION, close DOUBLE PRECISION,
            pre_close DOUBLE PRECISION, pct_chg DOUBLE PRECISION,
            vol DOUBLE PRECISION, amount DOUBLE PRECISION,
            total_mv DOUBLE PRECISION, turnover_rate DOUBLE PRECISION,
            is_st BOOLEAN,
            fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (ts_code, trade_date)
        )"""))
    db.execute(text("CREATE INDEX IF NOT EXISTS idx_mkt_bars_date ON mkt_bars_daily (trade_date)"))
    db.commit()


_UPSERT = """
INSERT INTO mkt_bars_daily (ts_code, trade_date, open, high, low, close, pre_close, pct_chg,
                            vol, amount, total_mv, turnover_rate, is_st)
VALUES (:ts_code, :trade_date, :open, :high, :low, :close, :pre_close, :pct_chg,
        :vol, :amount, :total_mv, :turnover_rate, :is_st)
ON CONFLICT (ts_code, trade_date) DO UPDATE
  SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close,
      pre_close=EXCLUDED.pre_close, pct_chg=EXCLUDED.pct_chg, vol=EXCLUDED.vol,
      amount=EXCLUDED.amount, total_mv=EXCLUDED.total_mv,
      turnover_rate=EXCLUDED.turnover_rate, is_st=EXCLUDED.is_st, fetched_at=now()
"""


def upsert_day(db, rows: Sequence[Dict[str, Any]]) -> int:
    from sqlalchemy import text
    n = 0
    for i in range(0, len(rows), BATCH):
        chunk = list(rows[i:i + BATCH])
        db.execute(text(_UPSERT), chunk)
        n += len(chunk)
    return n


def backfill(start8: str, end8: str, sleep_sec: float = 0.4, save: bool = True,
             days: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """逐日回填；每天一批 upsert，进度打印。返回汇总。"""
    ds = list(days) if days else trade_days(start8, end8)
    out: Dict[str, Any] = {"ok": True, "days": len(ds), "rows": 0, "errors": [], "skipped": []}
    if not ds:
        return {"ok": False, "reason": "no_trade_days"}
    db = None
    try:
        from app.database import SessionLocal
        db = SessionLocal()
        ensure_table(db)
        sts = st_map()
        print(f"[mkt_bars] 交易日 {len(ds)} 天（{ds[0]} → {ds[-1]}）｜ST 映射 {len(sts)} 只")
        for i, d8 in enumerate(ds, 1):
            try:
                rows = day_rows(d8, sts)
                if not rows:
                    out["skipped"].append(d8)
                    print(f"[mkt_bars] {i}/{len(ds)} {d8} 无数据 → 跳过")
                    continue
                n = upsert_day(db, rows) if save else len(rows)
                db.commit()
                out["rows"] += n
                print(f"[mkt_bars] {i}/{len(ds)} {d8} 写入 {n} 行（累计 {out['rows']}）", flush=True)
            except Exception as e:
                out["errors"].append("%s:%s" % (d8, str(e)[:70]))
                print(f"[mkt_bars] {i}/{len(ds)} {d8} 失败: {type(e).__name__}: {str(e)[:70]}")
                try:
                    db.rollback()
                except Exception:
                    pass
            time.sleep(max(0.0, float(sleep_sec)))
    except Exception as e:
        out["ok"] = False
        out["errors"].append("session:%s" % str(e)[:80])
    finally:
        try:
            if db is not None:
                db.close()
        except Exception:
            pass
    return out


def coverage() -> Dict[str, Any]:
    """覆盖度查询（回测前先看这个）。"""
    try:
        from app.database import SessionLocal
        from sqlalchemy import text
        db = SessionLocal()
        try:
            r = db.execute(text("""
                SELECT count(DISTINCT trade_date) AS days, count(*) AS rows,
                       min(trade_date) AS d0, max(trade_date) AS d1,
                       count(DISTINCT ts_code) AS codes
                FROM mkt_bars_daily""")).mappings().first()
            return dict(r) if r else {}
        finally:
            db.close()
    except Exception as e:
        return {"error": str(e)[:80]}
