# -*- coding: utf-8 -*-
"""fetch_moneyflow_history.py — 拉个股资金流历史（② 个股级资金确认的底座）。

口径：Tushare `moneyflow`（**按日拉全市场**：net_mf_amount 净流入额/万元 + 大单买卖额/万元）。
为什么按日：`moneyflow` 必须给 ts_code 或 trade_date；按日 = 1 天 1 次调用（5551 只），
比逐股调用少 5000×。

用法: .venv/bin/python jobs/fetch_moneyflow_history.py [--start 20251215] [--end 20260911] [--api moneyflow]
产出: .dsh-tmp/buyside/moneyflow_<api>.parquet（**可断点续跑**：已入库的日期跳过）
"""
import argparse
import os
import sys
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))
OUT_DIR = os.path.join(ROOT, ".dsh-tmp", "buyside")

FIELDS = {
    "moneyflow": "ts_code,trade_date,net_mf_amount,buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount",
    "moneyflow_dc": "trade_date,ts_code,net_amount,net_amount_rate,buy_elg_amount,buy_lg_amount",
}


def trading_days(start, end):
    bars = pd.read_parquet(os.path.join(OUT_DIR, "bars.parquet"), columns=["trade_date"])
    ds = sorted(set(bars["trade_date"].astype(str)))
    return [d for d in ds if start <= d <= end]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20251215")
    ap.add_argument("--end", default="20260911")
    ap.add_argument("--api", default="moneyflow")
    args = ap.parse_args()
    import importlib
    relay = importlib.import_module("tushare_relay")

    out = os.path.join(OUT_DIR, "moneyflow_%s.parquet" % args.api)
    done, rows = set(), []
    if os.path.exists(out):
        old = pd.read_parquet(out)
        rows = old.to_dict("records")
        done = set(old["trade_date"].astype(str).unique())
        print("[mf] 续跑：已有 %d 行 / %d 天" % (len(old), len(done)), flush=True)

    days = [d for d in trading_days(args.start, args.end) if d not in done]
    print("[mf] 待拉 %d 天（%s → %s）api=%s" % (len(days), args.start, args.end, args.api), flush=True)
    fails = []
    for k, d in enumerate(days, 1):
        t0 = time.time()
        try:
            fields, items = relay.relay_items(args.api, fields=FIELDS[args.api], trade_date=d)
            if not items:
                fails.append((d, "empty"))
                print("[mf] %s 空返回（记 failed，不当作'当天没有'）" % d, flush=True)
            else:
                idx = {n: i for i, n in enumerate(fields)}
                for it in items:
                    rows.append({n: it[idx[n]] for n in fields})
        except Exception as e:
            fails.append((d, "%s: %s" % (type(e).__name__, str(e)[:80])))
            print("[mf] %s 失败 %s" % (d, str(e)[:100]), flush=True)
        if k % 10 == 0 or k == len(days):
            pd.DataFrame(rows).to_parquet(out, index=False)
            print("[mf] %d/%d 已落盘（%.1fs/天）" % (k, len(days), (time.time() - t0)), flush=True)
        time.sleep(0.2)
    if rows:
        pd.DataFrame(rows).to_parquet(out, index=False)
    print("[mf] 完成：%d 行 → %s；失败 %d 天 %s" % (len(rows), out, len(fails), fails[:5]), flush=True)


if __name__ == "__main__":
    main()
