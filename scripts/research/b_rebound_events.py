#!/usr/bin/env python3
"""B反 case-series (read-only research script).

用途
----
把「狼大 B反 事件」按统一字段落成可复现的 case series：事件日、标的、A 段高低点、
目标位、失效位、口径来源，然后从本地 parquet 计算：
  1) 半分位 (H+L)/2 与目标位、前低的关系；
  2) 事件日之后的 MFE/MAE（1/3/5/10/20 交易日）；
  3) 目标先到还是失效先到；
  4) 是否突破前高（B反升级）还是止步于半分位（B反→C杀）；
  5) B反高点之后的 20 日回撤（C杀幅度）。

只读：只读 data/ 下的 parquet / json，不写数据库、不接交易、不接 gate。
数据缺口（脚本会打印）：stk_mins 无 8/3 分钟线；旧帖 7/10-7/30 不可读。

用法：.venv/bin/python scripts/research/b_rebound_events.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


@dataclass
class Event:
    key: str
    date: str            # 事件日（狼大给出判断/目标的交易日）
    symbol: str          # 标的（ETF 或指数）
    role: str            # 当时的口径：B反观察 / B反中段 / B反结束 / A杀 / 3-5末段
    quote: str           # 原话摘录（短）
    source: str          # 原文出处
    a_high: float | None  # A 段高点
    a_low: float | None   # A 段低点（事件日已知的那个低点）
    target: float | None
    invalid: float | None
    prior_low: float | None


EVENTS = [
    Event(
        key="2026-08-03_588170",
        date="20260803", symbol="588170.SH", role="B反观察(预期日)",
        quote="科创半导体ETF 的B反高点可能在1.15 后面我认为还有C杀",
        source="NGA 47288722 楼1271 / xlsx 历史存档 2026-08-03 11:20",
        a_high=1.465, a_low=0.837, target=1.15, invalid=0.820, prior_low=1.149,
    ),
    Event(
        key="2025-08-01_INDEX",
        date="20250801", symbol="000001.SH", role="弱B反(观察)",
        quote="指数新低……弱B反就持续了3小时 / B反没完 不要怕，等B反走完还是C",
        source="xlsx 2025 2025-08-01 12:56/13:26, 2025-08-04 09:38",
        a_high=3636.17, a_low=3550.04, target=3579.0, invalid=3547.16, prior_low=None,
    ),
    Event(
        key="2026-03-30_INDEX",
        date="20260330", symbol="000001.SH", role="B反中段(绞肉机)",
        quote="现在还是B反 / 整个B反周期都是绞肉机",
        source="xlsx 2026 2026-03-30 10:04/11:27",
        a_high=4197.20, a_low=3794.68, target=3955.0, invalid=3794.68, prior_low=None,
    ),
    Event(
        key="2026-04-20_INDEX",
        date="20260420", symbol="000001.SH", role="B反中段(有量不是高点)",
        quote="现在是B反过程 只不过有量目前就不是高点 缩量就容易出高点",
        source="xlsx 2026 2026-04-20 11:01",
        a_high=4197.20, a_low=3794.68, target=4210.0, invalid=3794.68, prior_low=None,
    ),
    Event(
        key="2026-05-11_INDEX",
        date="20260511", symbol="000001.SH", role="3-5末段(新高后三天)",
        quote="按指数目标和时间周期来说 今天过完新高后的三天内非常重要",
        source="wolf-daily-log-xls2026.md 2026-05-11",
        a_high=4258.86, a_low=3794.68, target=4258.86, invalid=4177.92, prior_low=None,
    ),
    Event(
        key="2025-09-04_INDEX",
        date="20250904", symbol="000001.SH", role="A杀日(3-2的A)",
        quote="主线A杀的话就不可能有指数新高。这不合理",
        source="xlsx 2025 2025-09-04 22:23",
        a_high=3885.31, a_low=3732.84, target=None, invalid=3761.0, prior_low=None,
    ),
]


def load_bars(symbol: str) -> pd.DataFrame:
    if symbol == "000001.SH":
        df = pd.DataFrame(json.load(open(DATA / "index_daily_000001.json")))
        df = df[["trade_date", "open", "high", "low", "close", "amount"]].copy()
        df["vol"] = df["amount"]
    else:
        allf = pd.read_parquet(DATA / "etf" / "etf_daily.parquet")
        df = allf[allf.ts_code == symbol][["trade_date", "open", "high", "low", "close", "vol"]].copy()
        df["amount"] = None
    df = df.sort_values("trade_date").reset_index(drop=True)
    df["trade_date"] = df["trade_date"].astype(str)
    return df


def study(ev: Event) -> dict:
    df = load_bars(ev.symbol)
    i = df.index[df.trade_date == ev.date]
    if len(i) == 0:
        return {"key": ev.key, "error": "event date not in bars"}
    i = int(i[0])
    t0 = df.iloc[i]
    fwd = df.iloc[i + 1:i + 21].reset_index(drop=True)
    out = dict(asdict(ev))
    out.update({
        "t0_close": round(float(t0.close), 4),
        "t0_high": round(float(t0.high), 4),
        "t0_low": round(float(t0.low), 4),
        "half_pos_event_low": round((ev.a_high + ev.a_low) / 2, 4) if ev.a_high and ev.a_low else None,
    })
    if ev.target:
        out["target_vs_half_pct"] = round((ev.target / out["half_pos_event_low"] - 1) * 100, 2) if out["half_pos_event_low"] else None
        out["target_vs_prior_low_pct"] = round((ev.target / ev.prior_low - 1) * 100, 2) if ev.prior_low else None
    # forward MFE / MAE
    for n in (1, 3, 5, 10, 20):
        w = fwd.head(n)
        if len(w):
            out[f"mfe_{n}d_pct"] = round((w.high.max() / t0.close - 1) * 100, 2)
            out[f"mae_{n}d_pct"] = round((w.low.min() / t0.close - 1) * 100, 2)
    # first touch: target vs invalid
    if ev.target and ev.invalid:
        hit_t = fwd.index[fwd.high >= ev.target]
        hit_i = fwd.index[fwd.close < ev.invalid]
        first_t = int(hit_t[0]) + 1 if len(hit_t) else None
        first_i = int(hit_i[0]) + 1 if len(hit_i) else None
        out["first_target_day"] = first_t
        out["first_invalid_day"] = first_i
        out["order"] = ("target_first" if first_t and (not first_i or first_t < first_i)
                        else "invalid_first" if first_i and (not first_t or first_i < first_t)
                        else "neither/20d")
    if ev.target and len(fwd):
        need = ev.target / float(t0.close) - 1
        got = float(fwd.high.max()) / float(t0.close) - 1
        out["target_progress_pct"] = round(min(got / need, 2.0) * 100, 1) if need > 0.01 else None
        out["peak_vs_target_pct"] = round((float(fwd.high.max()) / ev.target - 1) * 100, 2)
    # post-high drawdown within 20d
    if len(fwd):
        j = int(fwd.high.idxmax())
        peak = float(fwd.high.max())
        after = fwd.iloc[j + 1:]
        out["peak_day"] = j + 1
        out["peak_pct"] = round((peak / t0.close - 1) * 100, 2)
        out["post_peak_dd_pct"] = round((after.low.min() / peak - 1) * 100, 2) if len(after) else None
    return out


def main() -> None:
    rows = [study(e) for e in EVENTS]
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 50)
    cols = ["key", "role", "t0_close", "a_high", "a_low", "half_pos_event_low", "target",
            "target_vs_half_pct", "target_vs_prior_low_pct", "invalid",
            "mfe_5d_pct", "mae_5d_pct", "mfe_20d_pct", "mae_20d_pct", "target_progress_pct", "peak_vs_target_pct",
            "first_target_day", "first_invalid_day", "order", "peak_day", "peak_pct", "post_peak_dd_pct"]
    cols = [c for c in cols if c in df.columns]
    print(df[cols].to_string(index=False))
    print("\n--- notes ---")
    print("- a_low for 2026-08-03 is the 10:25 low 0.837 known at the 11:20 statement (daily low 0.828 came 14:56; 8/4 low 0.824).")
    print("- half_pos_event_low for 2026-08-03 = (1.465+0.837)/2 = 1.151 ≈ target 1.15.")
    print("- 2026-05-11 target is the prior peak itself (no separate target was given).")
    print("- Data gaps: no 588170 minute bars before 2026-08-03 09:30 outside the 1min cache; old NGA thread 45974302 unreadable (07-13..07-30 missing).")


if __name__ == "__main__":
    main()
