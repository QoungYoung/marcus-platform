# -*- coding: utf-8 -*-
"""生产行情高开低收：datahubco/promax 补 high/low，本地缓存。

data/bars_combined.parquet 只有 close/amount；
高/低缺失时按交易日调 Tushare relay 的 daily，缓存到 data/bars_high_low.parquet。
"""
from __future__ import annotations
import os, sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "data/bars_combined.parquet")
CACHE = os.path.join(ROOT, "data/bars_high_low.parquet")


def _relay():
    sys.path.insert(0, ROOT)
    from core.tushare_relay import get_relay
    return get_relay()


def _cache() -> pd.DataFrame:
    if os.path.exists(CACHE):
        try:
            return pd.read_parquet(CACHE)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()


def _save(rows):
    if not rows:
        return
    df = pd.DataFrame(rows)
    old = _cache()
    if len(old):
        df = pd.concat([old, df], ignore_index=True).drop_duplicates(["ts_code", "trade_date"], keep="last")
    df.to_parquet(CACHE, index=False)


def load_bars(min_date: str | None = None, latest_only: bool = False) -> pd.DataFrame:
    base = pd.read_parquet(BASE, columns=["ts_code", "trade_date", "close", "amount"])
    dates = sorted(base.trade_date.astype(str).unique())
    if latest_only:
        dates = dates[-1:]
    elif min_date:
        dates = [d for d in dates if d >= min_date]
    cache = _cache()
    have = set(cache.trade_date.astype(str).unique()) if len(cache) else set()
    missing = [d for d in dates if d not in have]
    rows = []
    if missing:
        try:
            pro = _relay()
            for i, d in enumerate(missing, 1):
                try:
                    x = pro.daily(trade_date=d, fields="ts_code,trade_date,high,low")
                    if x is not None and len(x):
                        rows.append(x)
                except Exception:
                    continue
                if i % 5 == 0:
                    _save(rows); rows = []
            _save(rows)
        except Exception:
            pass
    cache = _cache()
    if len(cache):
        base = base.merge(cache, on=["ts_code", "trade_date"], how="left")
    else:
        base["high"] = base["close"]; base["low"] = base["close"]
    if "high" not in base.columns: base["high"] = base["close"]
    if "low" not in base.columns: base["low"] = base["close"]
    base["high"] = base["high"].fillna(base["close"])
    base["low"] = base["low"].fillna(base["close"])
    return base


def latest_date() -> str:
    base = pd.read_parquet(BASE, columns=["trade_date"])
    return str(base.trade_date.astype(str).max())
