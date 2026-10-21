# -*- coding: utf-8 -*-
"""语义案例校验：状态是否复现狼大对风向标的判断，不看收益。"""
from __future__ import annotations
import os, sys
import pandas as pd
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend")); sys.path.insert(0, os.path.join(ROOT, "backend/app/services"))
from jobs.build_bellwether_state import compute_history

CASES = [
    ("2026-01-12", "军工/航天", "卫星互联网", "中国卫星", "600118.SH", "alive",
     "风向标没有死，自己的票没涨也拿着等补涨"),
    ("2026-01-16", "军工/航天", "卫星互联网", "中国卫星", "600118.SH", "not_dead",
     "先看风向，我分享过了"),
    ("2026-01-22", "半导体/芯片", "半导体设备", "中科飞测", "688361.SH", "not_dead",
     "先从风向标看起，再看核心有没有反方向"),
    ("2026-01-28", "半导体/芯片", "半导体设备", "北方华创", "002371.SZ", "not_dead",
     "半导体设备风向标被压制，核心能不跌就不错，很强"),
    ("2026-07-02", "AI/算力/科技", "光模块/CPO", "中际旭创", "300308.SZ", "not_dead",
     "等大哥表现，龙头核心风向标的用法分享过了"),
]

def main():
    bars = pd.read_parquet(os.path.join(ROOT, "data/bars_combined.parquet"),
                           columns=["ts_code","trade_date","close","amount"])
    hb = pd.read_parquet(os.path.join(ROOT, ".dsh-tmp/buyside/bars.parquet"),
                         columns=["ts_code","trade_date","high"])
    bars = bars.merge(hb, on=["ts_code","trade_date"], how="left")
    df = bars.pivot_table(index="trade_date", columns="ts_code", values="close", aggfunc="last").sort_index()
    high = bars.pivot_table(index="trade_date", columns="ts_code", values="high", aggfunc="last").sort_index()
    amt = bars.pivot_table(index="trade_date", columns="ts_code", values="amount", aggfunc="last").sort_index()
    rows = []
    for date, theme, sub, name, code, expected, quote in CASES:
        hist = compute_history(df, high, amt, sub, code, min_date="20251001")
        d8 = date.replace("-", "")
        got = next((r for r in hist if str(r["date"]) == d8), None)
        rows.append({"date": date, "theme": theme, "subtheme": sub, "bellwether": name,
                     "code": code, "expected": expected,
                     "predicted": got["state"] if got else "no_data",
                     "impulse": got.get("impulse") if got else None,
                     "quote": quote})
    out = pd.DataFrame(rows)
    out.to_csv(os.path.join(ROOT, "data/core_p1/semantic_cases.csv"), index=False, encoding="utf-8-sig")
    print(out[["date","subtheme","bellwether","expected","predicted","impulse"]].to_string(index=False))

if __name__ == "__main__":
    import jobs.build_bellwether_state  # noqa: F401
    main()
