# -*- coding: utf-8 -*-
"""bt_fetch_mins.py — 为回测拉**分钟数据**（个股/ETF + 指数），按 (symbol, date) 落地到本地缓存。

数据源（2026-09-15 实测）：
  · 个股/ETF：`stk_mins`（promax 中继）—— 支持 1min/5min、支持 start/end 区间（119 天 4.4s）；
  · 指数：`stk_mins` 也能给指数码（如 `000001.SH`，单日 48~49 根），本地 ClickHouse `a_share_mins` 同源但只覆盖近两周。

缓存：`<out>/<ts_code>_<freq>.json` = {"ts_code":…, "freq":…, "bars": [[time, o,h,l,c,vol,amount], …]}

用法（容器内）：
  python jobs/bt_fetch_mins.py --symbols SH600039,SH600284,... --days 20260911 [--freq 5min] \
      --out /app/data/_bt_full/mins [--index 000001.SH]
  python jobs/bt_fetch_mins.py --symbols-file /app/data/_bt_full/_summary/legs_all.jsonl --days 20260909-20260914
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path[:0] = ["/app", "/app/core"]


def to_ts(sym: str) -> str:
    s = str(sym).strip().upper()
    if "." in s:
        return s
    return (s[2:8] + "." + s[:2]) if s[:2] in ("SH", "SZ") else s


def fetch(ts_code: str, freq: str, day: str, tries: int = 3):
    import tushare_relay as R
    for i in range(tries):
        try:
            flds, items = R.relay_items(
                "stk_mins", fields="ts_code,trade_time,open,high,low,close,vol,amount",
                ts_code=ts_code, freq=freq, start_date=day, end_date=day)
            return items or []
        except Exception as e:
            if i == tries - 1:
                print("[mins] %s %s 失败: %s" % (ts_code, day, str(e)[:90]), file=sys.stderr)
            time.sleep(2 + 2 * i)
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--symbols-file", default="", help="jsonl，取其中 symbol 字段")
    ap.add_argument("--index", default="000001.SH")
    ap.add_argument("--days", required=True, help="YYYYMMDD 或 A-B 区间")
    ap.add_argument("--freq", default="5min")
    ap.add_argument("--out", default="/app/data/_bt_full/mins")
    a = ap.parse_args()

    syms = [x.strip() for x in a.symbols.split(",") if x.strip()]
    if a.symbols_file and os.path.exists(a.symbols_file):
        seen = set()
        for ln in open(a.symbols_file, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            s = o.get("symbol") or o.get("ts_code")
            if s and s not in seen:
                seen.add(s); syms.append(s)
    if a.index:
        syms.append(a.index)
    if "-" in a.days and len(a.days) == 17:
        d0, d1 = a.days.split("-")
        days = []
        import datetime as _dt
        x = _dt.date(int(d0[:4]), int(d0[4:6]), int(d0[6:8]))
        e = _dt.date(int(d1[:4]), int(d1[4:6]), int(d1[6:8]))
        while x <= e:
            if x.weekday() < 5:
                days.append(x.strftime("%Y%m%d"))
            x += _dt.timedelta(days=1)
    else:
        days = [a.days]
    os.makedirs(a.out, exist_ok=True)
    print("[mins] 标的 %d 个 × %d 天 × %s → %s" % (len(syms), len(days), a.freq, a.out), flush=True)
    n_new = n_bar = 0
    for sym in syms:
        ts = to_ts(sym)
        for d in days:
            path = os.path.join(a.out, "%s_%s_%s.json" % (ts.replace(".", "_"), a.freq, d))
            if os.path.exists(path):
                continue
            bars = fetch(ts, a.freq, d)
            if not bars:
                continue
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"ts_code": ts, "freq": a.freq, "date": d,
                           "bars": [list(b) for b in bars]}, f, ensure_ascii=False)
            n_new += 1; n_bar += len(bars)
            print("[mins] %-11s %s  %d 根" % (ts, d, len(bars)), flush=True)
    print("[mins] 完成：新写 %d 个文件 / %d 根 bar" % (n_new, n_bar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
