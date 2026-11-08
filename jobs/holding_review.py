#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""holding_review.py — 5 天持仓复核：只列每日涨幅，不替用户做买卖判断。

用法：
  python jobs/holding_review.py --symbol 688836.SH
  python jobs/holding_review.py --symbol 688836.SH --entry-date 20261017 --days 5
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "jobs"))

from mainline_common import last_trade_date, trade_dates, write_json  # noqa: E402
from core.tushare_relay import relay_query  # noqa: E402
from app.services.mainline_read import load_position_context  # noqa: E402


def to_ts(symbol: str) -> str:
    s = symbol.strip().upper()
    if "." in s:
        return s
    if s.startswith(("SH", "SZ", "BJ")):
        return s[2:] + "." + s[:2]
    if s.startswith(("6", "9")):
        return s + ".SH"
    if s.startswith(("0", "2", "3")):
        return s + ".SZ"
    return s + ".BJ"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--entry-date", default="")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    ts = to_ts(a.symbol)
    asof = last_trade_date(a.date)
    ds = trade_dates(a.date)
    if a.entry_date:
        entry = a.entry_date
    else:
        entry = ds[-a.days - 1] if len(ds) > a.days else ds[0]
    end = asof
    try:
        df = relay_query("daily", ts_code=ts, start_date=entry, end_date=end)
    except Exception as e:
        print(f"取日线失败: {e}", file=sys.stderr); sys.exit(1)
    if df is None or len(df) == 0:
        print(f"{ts} 无日线数据"); sys.exit(1)
    d = df.copy()
    d["trade_date"] = d["trade_date"].astype(str)
    d["pct_chg"] = [float(x or 0) for x in d["pct_chg"]]
    d["close"] = [float(x or 0) for x in d["close"]]
    d = d.sort_values("trade_date").tail(a.days)
    cum = 1.0
    rows = []
    for r in d.itertuples():
        cum *= (1 + r.pct_chg / 100.0)
        rows.append({"date": r.trade_date, "close": round(r.close, 3),
                     "pct_chg": round(r.pct_chg, 2), "cum_pct": round((cum - 1) * 100, 2)})
    ctx = load_position_context(symbol=ts)
    out = {"symbol": ts, "entry": entry, "as_of": asof, "days": a.days,
           "daily": rows, "context": {k: ctx.get(k) for k in
                                      ("theme_id", "theme_name", "verdict", "thesis_valid",
                                       "review_required", "failed_criteria", "caps", "holding_days")}}
    path = os.path.join(ROOT, "data", "mainline", asof, f"holding_review_{ts.replace('.', '_')}.json")
    write_json(path, out)
    print(f"{ts} {entry}~{asof} 持仓复核（{a.days} 天）")
    print("date | close | pct_chg | cum")
    print("---|---|---:|---:")
    for r in rows:
        print(f"{r['date']} | {r['close']} | {r['pct_chg']:+.2f}% | {r['cum_pct']:+.2f}%")
    if ctx.get("available"):
        print(f"context: {ctx.get('theme_name')} | verdict={ctx.get('verdict')} | "
              f"thesis_valid={ctx.get('thesis_valid')} | review_required={ctx.get('review_required')}")
    print(f"written: {path}")


if __name__ == "__main__":
    main()
