#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""mainline_common.py — P1 共用数据/评分工具（只读）。

- as-of 交易日
- SW L2 / CSI / 东财 指数日线
- 全市场 daily 快照（算宽度/leader RS）
- 百分位/z-score
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core.tushare_relay import relay_query  # noqa: E402


def trade_dates(date8: str, start: Optional[str] = None) -> List[str]:
    if start is None:
        start = (datetime.strptime(date8, "%Y%m%d") - timedelta(days=150)).strftime("%Y%m%d")
    cal = relay_query("trade_cal", exchange="SSE", start_date=start, end_date=date8, is_open="1")
    ds = sorted(str(x) for x in cal["cal_date"])
    if not ds:
        raise RuntimeError(f"no trade date <= {date8}")
    return ds


def last_trade_date(date8: str) -> str:
    return trade_dates(date8)[-1]


def _series(df, col: str = "close") -> pd.Series:
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    d = df.copy()
    d["trade_date"] = d["trade_date"].astype(str)
    d[col] = pd.to_numeric(d[col], errors="coerce")
    return d.set_index("trade_date")[col].sort_index()


def index_close(typ: str, code: str, start: str, end: str) -> pd.Series:
    if typ == "csi":
        df = relay_query("index_daily", ts_code=code, start_date=start, end_date=end)
    elif typ in {"sw_l2", "sw_l3"}:
        df = relay_query("sw_daily", ts_code=code, start_date=start, end_date=end)
    elif typ == "eastmoney":
        df = relay_query("dc_daily", ts_code=code, start_date=start, end_date=end)
    else:
        return pd.Series(dtype=float)
    return _series(df)


def index_amount(typ: str, code: str, start: str, end: str) -> pd.Series:
    if typ == "csi":
        df = relay_query("index_daily", ts_code=code, start_date=start, end_date=end)
    elif typ in {"sw_l2", "sw_l3"}:
        df = relay_query("sw_daily", ts_code=code, start_date=start, end_date=end)
    elif typ == "eastmoney":
        df = relay_query("dc_daily", ts_code=code, start_date=start, end_date=end)
    else:
        return pd.Series(dtype=float)
    if df is None or len(df) == 0:
        return pd.Series(dtype=float)
    d = df.copy()
    d["trade_date"] = d["trade_date"].astype(str)
    d["amount"] = pd.to_numeric(d["amount"], errors="coerce")
    return d.set_index("trade_date")["amount"].sort_index()


def daily_snapshot(date8: str) -> pd.DataFrame:
    df = relay_query("daily", trade_date=date8)
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["ts_code", "close"])
    d = df.copy()
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    return d[["ts_code", "close"]].dropna()


def member_map() -> Dict[str, set]:
    m = relay_query("index_member_all", limit=10000)
    out: Dict[str, set] = {}
    for col in ("l2_code", "l3_code"):
        for code, g in m.groupby(col):
            out[str(code)] = set(g["ts_code"].astype(str))
    return out


def pct(a: float, b: float) -> float:
    if not a or not b or b <= 0:
        return 0.0
    return a / b - 1.0


def zscores(vals: List[float]) -> List[float]:
    if not vals:
        return []
    mu = float(np.mean(vals))
    sd = float(np.std(vals))
    if sd <= 1e-12:
        return [0.0] * len(vals)
    return [(v - mu) / sd for v in vals]


def clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def write_json(path: str, obj: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
