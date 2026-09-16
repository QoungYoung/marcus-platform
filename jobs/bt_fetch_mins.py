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


# ⚠️ 同一进程内记住"stk_mins 不可用"（本机实测：promax 恒返回 `minute_data_pending`）：
#    否则每个标的都要先重试 3 次（sleep 2+4+6=12s）再走兜底 → 500 个标的要多花 ~1.7 小时。
_SKIP_STK_MINS = {"flag": False}


def fetch(ts_code: str, freq: str, day: str, tries: int = 3):
    """先 `stk_mins`（promax，历史全）；失败再退本地 ClickHouse `a_share_mins`
    （列序不同：ts_code,trade_time,freq,open,high,low,close,vol,amount —— 必须显式对齐，
    实测数据与 stk_mins 同源、逐 bar 一致）。"""
    import tushare_relay as R
    err = ""
    _tries = 1 if _SKIP_STK_MINS["flag"] else tries
    for i in range(_tries):
        try:
            flds, items = R.relay_items(
                "stk_mins", fields="ts_code,trade_time,open,high,low,close,vol,amount",
                ts_code=ts_code, freq=freq, start_date=day, end_date=day)
            if items:
                return items
        except Exception as e:
            err = str(e)[:90]
            if "minute_data_pending" in err or "全部数据源失败" in err:
                _SKIP_STK_MINS["flag"] = True     # 本进程后续不再浪费时间重试
                break
        time.sleep(1 + i)
    try:
        flds, items = R.relay_items("a_share_mins", ts_code=ts_code, freq=freq.upper(),
                                    start_date="%s-%s-%s 00:00:00" % (day[:4], day[4:6], day[6:8]),
                                    end_date="%s-%s-%s 23:59:59" % (day[:4], day[4:6], day[6:8]))
        if items:
            fi = {k: i for i, k in enumerate(flds or [])}
            out = []
            for b in items:
                out.append([b[fi.get("ts_code", 0)], b[fi.get("trade_time", 1)],
                            b[fi["open"]], b[fi["high"]], b[fi["low"]], b[fi["close"]],
                            b[fi.get("vol")], b[fi.get("amount")]])
            print("[mins] %s %s 走 a_share_mins 兜底 %d 根" % (ts_code, day, len(out)), file=sys.stderr)
            return out
    except Exception as e2:
        err = "%s | a_share_mins: %s" % (err, str(e2)[:70])
    print("[mins] %s %s 失败: %s" % (ts_code, day, err), file=sys.stderr)
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
