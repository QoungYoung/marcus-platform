# -*- coding: utf-8 -*-
"""
自上而下的主线筛选器 v0 —— 把「狼大」判定主线的准则量化成可打分、可回测的筛选器。

准则（自上而下，逐层过滤）：
  ① 抗跌/主升确定   — 行业指数相对大盘的超额收益 + 均线多头 + 突破前高（一浪→二浪→再新高）
  ② 资金/量能进场   — 行业成交额是否放量（新资金进场proxy）；机构未跑另需主力净流入（见说明）
  ③ 产业链形态完备   — 一级行业成分股覆盖多少二级子行业（上游/中游/下游成型）
  ④ 新逻辑(catalyst) — 本版用“量能异动 + 事件/新闻”标注为需 LLM/人工补充，不计入硬分。

【数据】全部来自本地 parquet（data/指数数据、data/股票数据/资金流向数据）：
  - ci_l1_daily  : 中证一级行业指数日线(30个行业, OHLC/amount/成分股, 2010-2026)
  - ci_l2_daily  : 中证二级行业指数日线(含 l2_name/成分股)
  - moneyflow_ind_dc : 东财行业资金流(2023-09起, 可选, 用于主力净流入)

用法：
  python scripts/theme_filter_wolf.py --date 2026-06-26 --top 12

输出：按综合分排序的主线候选行业表 + 各准则分解得分。
"""
from __future__ import annotations

import argparse
import ast
import numpy as np
import pandas as pd

BASE = "data/指数数据"
MF_BASE = "data/股票数据/资金流向数据"


def _toset(c):
    if isinstance(c, np.ndarray):
        return set(c.tolist())
    if isinstance(c, (list, tuple)):
        return set(c)
    if isinstance(c, str):
        c = c.strip()
        if not c:
            return set()
        try:
            return set(ast.literal_eval(c))
        except Exception:
            return set()
    return set()


def load_ci1():
    return pd.read_parquet(f"{BASE}/ci_l1_daily.parquet")


def load_ci2():
    return pd.read_parquet(f"{BASE}/ci_l2_daily.parquet")


def build_stock_to_l2(ci2, date):
    """从 ci_l2 在 date 的快照构建 stock -> set(l2_name) 映射。"""
    sub = ci2.xs(date, level="trade_date")
    mapping = {}
    for name, codes in zip(sub["l2_name"], sub["con_codes"]):
        for c in _toset(codes):
            mapping.setdefault(c, set()).add(name)
    return mapping


def industry_breadth(ci2, constituents, date):
    if not constituents:
        return 0
    mapping = build_stock_to_l2(ci2, date)
    subs = set()
    for c in constituents:
        if c in mapping:
            subs |= mapping[c]
    return len(subs)


def compute_metrics(ci1, ci2, date, lookback=120):
    rows = []
    d = pd.Timestamp(date)
    dates = sorted(set(ci1.index.get_level_values("trade_date")))
    hist = [x for x in dates if x <= d][-(lookback + 1):]
    if len(hist) < 70:
        raise ValueError(f"日期 {date} 附近历史不足")
    h = pd.DatetimeIndex(hist)
    sub = ci1.loc[h]

    by_code = {}
    for (td, code), row in sub.iterrows():
        by_code.setdefault(code, {})[td] = row

    mkt_pct = sub.groupby(level="trade_date")["pct_change"].mean()

    for code in sorted(by_code.keys()):
        s = by_code[code]
        dates_seq = sorted(s.keys())
        closes = pd.Series({td: s[td]["close"] for td in dates_seq}).sort_index()
        amounts = pd.Series({td: s[td]["amount"] for td in dates_seq}).sort_index()
        name = s[dates_seq[-1]]["l1_name"]
        if len(closes) < 65:
            continue
        c_now = closes.iloc[-1]
        c20 = closes.iloc[-21] if len(closes) >= 21 else closes.iloc[0]
        c60 = closes.iloc[-61] if len(closes) >= 61 else closes.iloc[0]
        r20 = c_now / c20 - 1 if c20 else 0.0
        r60 = c_now / c60 - 1 if c60 else 0.0

        mkt_win = mkt_pct[mkt_pct.index.isin(closes.index)]
        er20 = r20 - mkt_win.iloc[-21:].mean() * 20 if len(mkt_win) >= 21 else r20
        er60 = r60 - mkt_win.iloc[-61:].mean() * 60 if len(mkt_win) >= 61 else r60

        ma20 = closes.iloc[-20:].mean()
        ma60 = closes.iloc[-60:].mean()
        trend = int(c_now > ma20 > ma60)

        prior_high = closes.iloc[-60:-20].max() if len(closes) >= 60 else closes.max()
        breakout = int(c_now >= prior_high)

        low_recent = closes.iloc[-20:].min()
        low_prior = closes.iloc[-60:-20].min() if len(closes) >= 60 else low_recent
        higher_low = int(low_recent > low_prior)

        amt_now = amounts.iloc[-5:].mean()
        amt_hist = amounts.iloc[-60:].mean()
        vol_expand = amt_now / amt_hist if amt_hist else 0.0

        breadth = industry_breadth(ci2, _toset(s[dates_seq[-1]]["con_codes"]), date)

        rows.append({
            "ts_code": code, "l1_name": name, "close": c_now, "r20": r20, "r60": r60,
            "er20": er20, "er60": er60, "trend": trend, "breakout": breakout,
            "higher_low": higher_low, "vol_expand": vol_expand, "breadth": breadth,
        })

    return pd.DataFrame(rows)


def score(df):
    def z(s):
        s = pd.to_numeric(s, errors="coerce")
        if s.std() == 0 or s.isna().all():
            return pd.Series(0.0, index=s.index)
        return (s - s.mean()) / s.std()

    df = df.copy()
    df["z_er20"] = z(df["er20"])
    df["z_er60"] = z(df["er60"])
    df["z_vol"] = z(df["vol_expand"])
    df["z_breadth"] = z(df["breadth"])

    df["score_main"] = (
        0.4 * df["z_er20"] + 0.3 * df["z_er60"]
        + 0.15 * df["trend"] + 0.1 * df["breakout"] + 0.05 * df["higher_low"]
    )
    df["score_money"] = df["z_vol"]
    df["score_chain"] = df["z_breadth"]
    df["composite"] = 0.5 * df["score_main"] + 0.25 * df["score_money"] + 0.25 * df["score_chain"]
    return df


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-06-26")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    ci1 = load_ci1()
    ci2 = load_ci2()
    df = compute_metrics(ci1, ci2, args.date)
    if df.empty:
        print("无数据")
        return
    df = score(df).sort_values("composite", ascending=False)
    show = df.head(args.top)[
        ["l1_name", "ts_code", "close", "r20", "er20", "trend", "breakout",
         "higher_low", "vol_expand", "breadth", "score_main", "score_money",
         "score_chain", "composite"]
    ].round(3)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)
    print(f"=== 自上而下主线筛选器 v0 @ {args.date} (按综合分排序 TOP{args.top}) ===")
    print(show.to_string(index=False))
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"\n[已保存] {args.out}")


if __name__ == "__main__":
    main()
