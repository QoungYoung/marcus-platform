"""B反 案例表复算（只读研究脚本，不接生产/不写库）。

用途：复现 docs/b-rebound-reread-202610.md 里的数值（折算、目标锚、案例特征、确认时点）。
数据：走 core.tushare_relay 拉 fund_daily / index_daily；已缓存则直接读 data/research/。

用法：
    python scripts/research_brebound_cases.py            # 有缓存用缓存
    python scripts/research_brebound_cases.py --refresh  # 重新拉数
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "research")
sys.path.insert(0, ROOT)

# 588170：2026-07-06 首日按新价交易，前一日收盘 3.579 折算为 1.193（约 3:1）
SPLIT_FIRST_DAY = "20260706"
SPLIT_RATIO = 1.0 / 3.0

CASES = [
    "20250801",  # 弱B反（指数 ABC 的 B）
    "20250904",  # 3-2 的 A 浪 / 当日恐慌低点
    "20260330",  # B反 中段（3900 收费站）
    "20260420",  # B反 后段（有量不是高点）
    "20260511",  # B反 延申 / 改判 3-5
    "20260521",  # B反 结束（当日恐慌）
    "20260803",  # 预期日（1.15 目标首次给出）
    "20260806",  # 指数 3 天站上 MA5 确认日
    "20260818",  # 目标区高点（1.107）
    "20260819",  # C杀 起点
]


def _cache(name: str) -> str:
    return os.path.join(OUT, name)


def load(name: str, fetch, refresh: bool) -> pd.DataFrame:
    path = _cache(name)
    if os.path.exists(path) and not refresh:
        return pd.read_csv(path, dtype={"trade_date": str})
    df = fetch()
    os.makedirs(OUT, exist_ok=True)
    df.to_csv(path, index=False)
    return df


def _fund(code: str):
    from core.tushare_relay import relay_query

    return relay_query(
        "fund_daily", ts_code=code, start_date="20250101", end_date="20261031"
    ).sort_values("trade_date").reset_index(drop=True)


def _index():
    from core.tushare_relay import relay_query

    return relay_query(
        "index_daily", ts_code="000001.SH", start_date="20250601", end_date="20261031"
    ).sort_values("trade_date").reset_index(drop=True)


def adjust_split(df: pd.DataFrame) -> pd.DataFrame:
    """把折算前的价格除以 3、成交量乘 3，得到连续序列（仅供研究口径）。"""
    d = df.copy()
    pre = d.trade_date < SPLIT_FIRST_DAY
    for col in ("open", "high", "low", "close"):
        if col in d:
            d.loc[pre, col] = d.loc[pre, col] * SPLIT_RATIO
    if "vol" in d:
        d.loc[pre, "vol"] = d.loc[pre, "vol"] / SPLIT_RATIO
    return d


def features(df: pd.DataFrame, day: str) -> dict | None:
    pos = np.where(df.trade_date.values == day)[0]
    if not len(pos):
        return None
    i = int(pos[0])
    close, high, low = df.close.values, df.high.values, df.low.values
    c = float(close[i])
    a60 = max(0, i - 59)
    lo20 = float(low[max(0, i - 19): i + 1].min())
    row = {
        "date": day,
        "close": round(c, 4),
        "hi60": round(float(close[a60: i + 1].max()), 4),
        "off_hi60_pct": round((c / float(close[a60: i + 1].max()) - 1) * 100, 2),
        "vs_ma5_pct": round((c / close[max(0, i - 4): i + 1].mean() - 1) * 100, 2),
        "vs_ma20_pct": round((c / close[max(0, i - 19): i + 1].mean() - 1) * 100, 2),
        "gap_lo20_pct": round((c / lo20 - 1) * 100, 2),
        "vol_x20": round(float(df.vol.values[i] / df.vol.values[max(0, i - 19): i + 1].mean()), 2),
    }
    for k in (5, 10, 20):
        row[f"r{k}"] = (
            round((close[i + k] / c - 1) * 100, 2) if i + k < len(df) else None
        )
    if i + 10 < len(df):
        row["min_lo_20d_pct"] = round((low[i + 1: i + 21].min() / c - 1) * 100, 2)
        row["max_hi_20d_pct"] = round((high[i + 1: i + 21].max() / c - 1) * 100, 2)
    return row


def anchors(etf_adj: pd.DataFrame, low: float) -> pd.DataFrame:
    """1.15 的候选锚：A 段高点的不同口径 + 50% 半分位 + 前低。"""
    hi_intraday = float(etf_adj.loc[etf_adj.trade_date == "20260701", "high"].iloc[0])
    hi_close = float(etf_adj.loc[etf_adj.trade_date == "20260709", "close"].iloc[0])
    hi_ps_intraday = float(etf_adj.loc[etf_adj.trade_date == "20260710", "high"].iloc[0])
    prior_low = float(etf_adj.loc[etf_adj.trade_date == "20260706", "low"].iloc[0])
    rows = [
        ("A高点(折算后盘中 7/1)", hi_intraday, None),
        ("A高点(折算后收盘 7/9)", hi_close, None),
        ("A高点(折算后盘中 7/10)", hi_ps_intraday, None),
        ("50%半分位(盘中高点)", None, round(low + (hi_intraday - low) * 0.5, 4)),
        ("50%半分位(收盘高点)", None, round(low + (hi_close - low) * 0.5, 4)),
        ("50%半分位(盘中高点 7/10)", None, round(low + (hi_ps_intraday - low) * 0.5, 4)),
        ("折算后首根前低(7/6)", prior_low, None),
    ]
    return pd.DataFrame(rows, columns=["anchor", "value", "halfway"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    etf_raw = load("fund_daily_588170.csv", lambda: _fund("588170.SH"), args.refresh)
    etf_adj = adjust_split(etf_raw)
    idx = load("index_daily_000001.csv", _index, args.refresh)

    os.makedirs(OUT, exist_ok=True)
    etf_adj.to_csv(os.path.join(OUT, "fund_daily_588170_split_adj.csv"), index=False)

    rows = []
    for d in CASES:
        for name, df in (("ETF588170", etf_adj), ("IDX000001", idx)):
            r = features(df, d)
            if r:
                rows.append({"mkt": name, **r})
    tbl = pd.DataFrame(rows)
    tbl.to_csv(os.path.join(OUT, "b_rebound_cases.csv"), index=False, encoding="utf-8-sig")

    pd.set_option("display.width", 240)
    print("== 案例特征（ETF 为折算后连续价）==")
    print(tbl.to_string(index=False))

    low = float(etf_adj.loc[etf_adj.trade_date == "20260803", "low"].iloc[0])
    print(f"\n== 1.15 候选锚（B起点低点={low}）==")
    print(anchors(etf_adj, low).to_string(index=False))

    peak = etf_adj[(etf_adj.trade_date >= "20260804") & (etf_adj.trade_date <= "20260831")].high.max()
    hi_a = float(etf_adj.loc[etf_adj.trade_date == "20260701", "high"].iloc[0])
    print(
        f"\n实际B反高点 {peak}；回撤到 A 段的 {(peak - low) / (hi_a - low) * 100:.1f}%；"
        f"距 1.1465 为 {(peak / 1.1465 - 1) * 100:.2f}%"
    )

    # 指数「3 天站上 5 日线」确认时点
    idx = idx.copy()
    idx["ma5"] = idx.close.rolling(5).mean()
    streak, out = 0, []
    for v in (idx.close > idx.ma5).values:
        streak = streak + 1 if v else 0
        out.append(streak)
    idx["streak_above_ma5"] = out
    conf = idx[(idx.trade_date >= "20260801") & (idx.trade_date <= "20260812")]
    print("\n== 指数 MA5 连续站上（4-4 确认口径）==")
    print(conf[["trade_date", "close", "ma5", "streak_above_ma5"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
