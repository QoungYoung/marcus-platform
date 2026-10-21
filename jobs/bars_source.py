# -*- coding: utf-8 -*-
"""行情源：优先 datahubco/promax daily，不依赖本地 bars_combined.parquet。

- 有 data/bars_combined.parquet：只补 high/low
- 没有：按日期调 daily 取 close/high/low/amount，缓存 data/bars_api.parquet
"""
from __future__ import annotations
import os, sys
from datetime import datetime, timedelta
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "data/bars_combined.parquet")
API_CACHE = os.path.join(ROOT, "data/bars_api.parquet")
HL_CACHE = os.path.join(ROOT, "data/bars_high_low.parquet")
FIELDS = "ts_code,trade_date,close,amount,high,low"


def _relay():
    sys.path.insert(0, ROOT)
    from core.tushare_relay import get_relay
    return get_relay()


def _read(p, filters=None):
    try:
        if not os.path.exists(p): return pd.DataFrame()
        return pd.read_parquet(p, filters=filters) if filters else pd.read_parquet(p)
    except Exception:
        try:
            return pd.read_parquet(p) if os.path.exists(p) else pd.DataFrame()
        except Exception:
            return pd.DataFrame()


def _write(p, frames):
    frames = [x for x in frames if x is not None and len(x)]
    if not frames: return
    df = pd.concat(frames, ignore_index=True)
    df["trade_date"] = df["trade_date"].astype(str)
    df = df.drop_duplicates(["ts_code", "trade_date"], keep="last")
    old = _read(p)
    if len(old):
        old["trade_date"] = old["trade_date"].astype(str)
        df = pd.concat([old, df], ignore_index=True).drop_duplicates(["ts_code", "trade_date"], keep="last")
    df.to_parquet(p, index=False)


def _weekdays(start=None, end=None):
    end = end or datetime.now().strftime("%Y%m%d")
    start = start or (datetime.strptime(end, "%Y%m%d") - timedelta(days=30)).strftime("%Y%m%d")
    d = datetime.strptime(start, "%Y%m%d"); e = datetime.strptime(end, "%Y%m%d")
    out = []
    while d <= e:
        if d.weekday() < 5: out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out


def _filter_codes(df, codes):
    if codes and df is not None and len(df):
        return df[df.ts_code.isin(codes)].copy()
    return df


def _fetch_daily(dates, codes=None):
    cache = _read(API_CACHE)
    if codes and len(cache):
        cache = cache[cache.ts_code.isin(codes)].copy()
    have = set(cache.trade_date.astype(str)) if len(cache) else set()
    missing = [d for d in dates if d not in have]
    if missing:
        try:
            pro = _relay(); rows = []
            for i, d in enumerate(missing, 1):
                try:
                    x = pro.daily(trade_date=d, fields=FIELDS)
                    x = _filter_codes(x, codes)
                    if x is not None and len(x): rows.append(x)
                except Exception:
                    continue
                if i % 10 == 0: _write(API_CACHE, rows); rows = []
            _write(API_CACHE, rows)
        except Exception:
            pass
    return _filter_codes(_read(API_CACHE), codes)


def load_bars(min_date: str | None = None, latest_only: bool = False, codes=None) -> pd.DataFrame:
    filters = []
    if codes:
        filters.append(("ts_code", "in", list(codes)))
    if min_date and not latest_only:
        filters.append(("trade_date", ">=", min_date))
    base = _read(BASE, filters=filters or None)
    if codes and len(base):
        base = base[base.ts_code.isin(codes)].copy()
    if len(base):
        dates = sorted(base.trade_date.astype(str).unique())
        if latest_only: dates = dates[-1:]
        elif min_date: dates = [d for d in dates if d >= min_date]
        hl = _read(HL_CACHE)
        have = set(hl.trade_date.astype(str)) if len(hl) else set()
        missing = [d for d in dates if d not in have]
        if missing:
            try:
                pro = _relay(); rows = []
                for i, d in enumerate(missing, 1):
                    try:
                        x = pro.daily(trade_date=d, fields="ts_code,trade_date,high,low")
                        if x is not None and len(x): rows.append(x)
                    except Exception:
                        continue
                    if i % 10 == 0: _write(HL_CACHE, rows); rows = []
                _write(HL_CACHE, rows)
            except Exception:
                pass
        hl = _read(HL_CACHE)
        out = base.merge(hl, on=["ts_code", "trade_date"], how="left") if len(hl) else base.copy()
        if "high" not in out: out["high"] = out["close"]
        if "low" not in out: out["low"] = out["close"]
        out["high"] = out["high"].fillna(out["close"]); out["low"] = out["low"].fillna(out["close"])
        return out

    # 没有基础 parquet：直接走 Tushare daily
    if latest_only:
        dates = _weekdays()[-15:]
        api = _fetch_daily(dates, codes)
        if len(api):
            api = api.sort_values("trade_date")
            last = str(api.trade_date.max())
            return api[api.trade_date == last].copy()
        return pd.DataFrame(columns=["ts_code","trade_date","close","amount","high","low"])
    dates = _weekdays(min_date or "20260601", datetime.now().strftime("%Y%m%d"))
    return _fetch_daily(dates, codes)


def latest_date() -> str:
    base = _read(BASE)
    if len(base): return str(base.trade_date.astype(str).max())
    api = _read(API_CACHE)
    if len(api): return str(api.trade_date.astype(str).max())
    return datetime.now().strftime("%Y%m%d")
