# -*- coding: utf-8 -*-
"""用本地 `data/指数历史分钟/<year>_5min/<code>_<year>.csv`（5 分钟）聚合出**长历史指数日线**，
写到 `apps/main_line/wave_level.py` 期望的 `data/指数数据/index_daily/<code>.{csv,parquet}`。

为什么：wave_level 需要几百根日线做结构判定，而 `data/index_daily_000001.json` 只覆盖 2025-01 起；
brze/tushare relay 的 index_daily 当前 tenant key 过期 → 改用本地 5 分钟数据自聚（2016 起）。
重叠区间（2025-01-01 之后）优先用官方日线 json（更准）。
用法: .venv/bin/python jobs/build_index_daily_from_5min.py
"""
import json, os, sys
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "指数历史分钟"
OUT = ROOT / "data" / "指数数据" / "index_daily"
CODE, CODE6 = "000001.SH", "000001"


def main() -> int:
    frames = []
    for y in range(2016, 2026):
        p = SRC / ("%d_5min" % y) / ("%s_%d.csv" % (CODE6, y))
        if not p.exists():
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        df.columns = [c.strip() for c in df.columns]
        df["trade_date"] = pd.to_datetime(df["时间"].astype(str).str[:10])
        g = df.groupby("trade_date")
        frames.append(pd.DataFrame({
            "open": g["开盘价"].first(), "high": g["最高价"].max(),
            "low": g["最低价"].min(), "close": g["收盘价"].last(),
            "amount": g["成交额"].sum()}))
        print("  %d: %d 交易日" % (y, len(frames[-1])))
    if not frames:
        print("没找到分钟文件"); return 2
    d = pd.concat(frames).sort_index()
    d = d[~d.index.duplicated(keep="last")]
    d = d[d.index < pd.Timestamp("2025-01-01")]
    # 官方日线（2025-01-02→）追加
    j = json.load(open(ROOT / "data" / "index_daily_000001.json", encoding="utf-8"))
    off = pd.DataFrame([{"trade_date": pd.to_datetime(str(r["trade_date"]).replace("-", ""), format="%Y%m%d"),
                         "close": float(r["close"]), "amount": float(r.get("amount") or 0)} for r in j])
    off = off.set_index("trade_date").sort_index()
    full = pd.concat([d, off]).sort_index()
    full = full[~full.index.duplicated(keep="last")]
    OUT.mkdir(parents=True, exist_ok=True)
    out_csv, out_parq = OUT / ("%s.csv" % CODE), OUT / ("%s.parquet" % CODE)
    full.reset_index().rename(columns={"index": "trade_date"}).to_csv(out_csv, index=False)
    full[["close"]].to_parquet(out_parq)
    print("写出 %d 行  %s → %s" % (len(full), full.index[0].date(), full.index[-1].date()))
    print("  ", out_csv, "|", out_parq)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
