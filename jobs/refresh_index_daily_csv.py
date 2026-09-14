# -*- coding: utf-8 -*-
"""增量刷新指数日线（datahubco+promax 中继）→ 合并写回 `data/指数数据/index_daily/<code>.{csv,parquet}`。

为什么：`wave_agent` 的日期 = 该 CSV 的**最后一根**；此前 16:30 刷新链走的是已失效的 relay
（`tenant key expired`）→ 日线停更 → 浪型判定跟着停更（2026-09-14 实测停在 09-10）。
本脚本改用仓库既有的 `apps/main_line/wave_agent._ts_pro()`（2026-09-13 起走 **datahubco+promax**）。

**合并语义（安全）**：读现有文件 → 与新拉取的行做 **union + 按日期去重（新值优先）+ 排序** 后写回。
绝不直接覆盖（教训：2026-09-14 曾用重建文件覆盖生产 CSV，把 09-02~09-10 截断掉）。

用法: python jobs/refresh_index_daily.py [--code 000001.SH] [--start 20260801] [--dry]
"""
import argparse, os, sys
from pathlib import Path

ROOT = "/app" if os.path.isdir("/app/app") else str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))
sys.path.insert(0, os.path.join(ROOT, "backend"))
DATA = os.environ.get("DATA_DIR", os.path.join(ROOT, "data"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000001.SH")
    ap.add_argument("--start", default="20260801")
    ap.add_argument("--dir", default=os.path.join(DATA, "指数数据", "index_daily"))
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    import pandas as pd
    from datetime import datetime
    import wave_agent as wa

    csv_p = os.path.join(args.dir, "%s.csv" % args.code)
    parq_p = os.path.join(args.dir, "%s.parquet" % args.code)
    old = pd.read_csv(csv_p, parse_dates=["trade_date"]) if os.path.exists(csv_p) else pd.DataFrame()
    if len(old):
        print("[refresh] 现有 %d 行，最后一天 %s" % (len(old), str(old["trade_date"].max())[:10]))
    pro = wa._ts_pro()
    df = pro.index_daily(ts_code=args.code, start_date=args.start,
                         end_date=datetime.now().strftime("%Y%m%d"))
    if df is None or not len(df):
        print("[refresh] 新拉取为空（源不可用？）"); return 2
    df = df.copy()
    df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d")
    df = df.sort_values("trade_date")
    print("[refresh] 新拉 %d 行，%s → %s" % (len(df), str(df["trade_date"].min())[:10], str(df["trade_date"].max())[:10]))
    merged = pd.concat([old, df]).drop_duplicates(subset=["trade_date"], keep="last").sort_values("trade_date")
    print("[refresh] 合并后 %d 行，%s → %s" % (len(merged), str(merged["trade_date"].min())[:10], str(merged["trade_date"].max())[:10]))
    if args.dry:
        print("[refresh] --dry，不写文件"); return 0
    os.makedirs(args.dir, exist_ok=True)
    keep = [c for c in ("trade_date", "open", "high", "low", "close", "vol", "amount",
                        "pre_close", "pct_chg", "change") if c in merged.columns]
    merged[keep].to_csv(csv_p, index=False)
    try:                                   # 容器里可能没 pyarrow/fastparquet（CSV 才是主口径）
        merged[["trade_date", "close"]].set_index("trade_date").to_parquet(parq_p)
    except Exception as _e:
        print("[refresh] parquet 跳过（%s），CSV 已更新" % type(_e).__name__)
    print("[refresh] 写出 %s | %s" % (csv_p, parq_p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
