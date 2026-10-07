# -*- coding: utf-8 -*-
"""补长历史**上证指数日线** → data/指数数据/index_daily/000001.SH.{csv,parquet}

为什么需要：`apps/main_line/wave_level.py::judge_wave()` 开头硬判
`os.path.exists('data/指数数据/index_daily/000001.SH.parquet')`，文件不存在就返回 **"无数据"**
（这就是路线 C 里 144 个交易日全返回"无"的原因 —— 不是算不出来，是**文件不在**）。
数据源：brze/tushare relay 的 `index_daily`（与 m5 预取同一通道）。

用法: .venv/bin/python jobs/backfill_index_daily.py [--start 20140101] [--code 000001.SH]
"""
import argparse, os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000001.SH")
    ap.add_argument("--start", default="20140101")
    ap.add_argument("--end", default="")
    ap.add_argument("--outdir", default=str(ROOT / "data" / "指数数据" / "index_daily"))
    args = ap.parse_args()
    import pandas as pd
    from app.services.t_backtest_data import _get_brze_pro, _brze_rate_limit
    from datetime import datetime
    end = args.end or datetime.now().strftime("%Y%m%d")
    _brze_rate_limit()
    df = _get_brze_pro().index_daily(ts_code=args.code, start_date=args.start, end_date=end)
    if df is None or not len(df):
        print("[index] 拉取为空，未写文件"); return 2
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df.sort_values("trade_date").reset_index(drop=True)
    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    csv_p = out / ("%s.csv" % args.code); parq_p = out / ("%s.parquet" % args.code)
    df.to_csv(csv_p, index=False)
    df[["trade_date", "close"]].set_index("trade_date").to_parquet(parq_p)
    print("[index] %s %d 行  %s → %s" % (args.code, len(df),
          df["trade_date"].iloc[0].date(), df["trade_date"].iloc[-1].date()))
    print("[index] 写出:", csv_p, "|", parq_p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
