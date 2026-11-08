#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""index_data_chain.py — 指数数据统一链（PG 唯一 canonical）。

拉 5 个指数 → upsert 到 PG index_daily → 自愈 mkt_bars_daily。不写本地行情文件。
数据源：东财 push2his 优先，失败退 Tushare 中继。
"""
from __future__ import annotations
import argparse, json, os, sys, time, urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "core"))
sys.path.insert(0, os.path.join(ROOT, "backend"))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))
CODES = ["000001.SH", "399001.SZ", "399006.SZ", "000300.SH", "000852.SH"]


def em_secid(code: str) -> str:
    n, m = code.split(".")
    return ("1." if m == "SH" else "0.") + n


def fetch_em(code: str, days: int):
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=%s"
           "&fields1=f1,f2,f3,f4,f5&fields2=f51,f52,f53,f54,f55,f56,f57&klt=101&fqt=1&end=20500101&lmt=%d"
           % (em_secid(code), days))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    j = json.loads(urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore"))
    out = []
    for line in (((j.get("data") or {}).get("klines")) or []):
        p = line.split(",")
        out.append({"date": p[0].replace("-", ""), "open": float(p[1]), "high": float(p[3]),
                    "low": float(p[4]), "close": float(p[2]), "vol": float(p[5]), "amount": float(p[6])})
    return out, "eastmoney:push2his"


def fetch_relay(code: str, days: int):
    from tushare_relay import relay_query
    end = datetime.now().strftime("%Y%m%d")
    start = time.strftime("%Y%m%d", time.localtime(time.time() - days * 86400 * 1.6))
    df = relay_query("index_daily", ts_code=code, start_date=start, end_date=end)
    out = []
    for _, r in (df.iterrows() if df is not None else []):
        cl = float(r["close"])
        out.append({"date": str(r["trade_date"]), "open": float(r.get("open") or cl),
                    "high": float(r.get("high") or cl), "low": float(r.get("low") or cl),
                    "close": cl, "vol": float(r.get("vol") or 0), "amount": float(r.get("amount") or 0)})
    return sorted(out, key=lambda x: x["date"]), "tushare_relay"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1500)
    ap.add_argument("--only", default="")
    ap.add_argument("--skip-db", action="store_true")
    ap.add_argument("--skip-mkt", action="store_true")
    a = ap.parse_args()
    codes = [x.strip() for x in a.only.split(",") if x.strip()] or list(CODES)
    total = 0
    for code in codes:
        old = {}
        try:
            from app.services.index_daily_store import load_bars
            old = {x["date"]: x for x in load_bars(code)}
        except Exception:
            old = {}
        rows, src = [], ""
        try:
            rows, src = fetch_em(code, a.days)
        except Exception as e:
            print(f"[index] {code} 东财失败 {type(e).__name__}: {str(e)[:80]} → 中继", flush=True)
        if not rows:
            try:
                rows, src = fetch_relay(code, a.days)
            except Exception as e:
                print(f"[index] {code} 中继失败 {type(e).__name__}: {str(e)[:80]}", flush=True)
        for x in rows:
            old[x["date"]] = x
        merged = [old[k] for k in sorted(old)]
        if not merged:
            print(f"[index] {code} 无数据", flush=True)
            continue
        if a.skip_db:
            print(f"[index] {code} skip-db n={len(merged)} src={src}", flush=True)
        else:
            from app.services.index_daily_store import upsert_rows
            n = upsert_rows(code, merged, src or "existing")
            print(f"[index] {code} upsert={n}/{len(merged)} {merged[0]['date']}→{merged[-1]['date']} src={src}", flush=True)
        total += 1
    if not a.skip_mkt:
        try:
            from app.services import mkt_bars as MB
            print("[index] PG mkt_bars:", MB.ensure_fresh(quiet=False), flush=True)
        except Exception as e:
            print(f"[index] mkt_bars 同步失败(不影响指数): {str(e)[:120]}", flush=True)
    print(f"[index] done codes={total}/{len(codes)}", flush=True)
    return 0 if total else 1


if __name__ == "__main__":
    raise SystemExit(main())
