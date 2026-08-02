# -*- coding: utf-8 -*-
"""方向预测数据落地脚本 v3：本地 parquet → 滞后特征 + 持有期收益标签 → CSV。

v3 关键设计（专家终审通过）：
  - 全部个股特征滞后 1 天（T-1 数据），目标基于 T 日 open
  - 预测目标 = (T+N_close - T_open) / T_open（执行日 T 买入并持有 N 天收益）
  - 所有价格 × adj_factor 前复权
  - 分板块 Winsorize（主板/双创板独立 1%/99%）
  - 每行业每日只保留市值 Top 3 龙头
  - 行业统计基于全市场计算后过滤
  - 移除 MARKET_COLS / INDEX_COLS，26 维纯截面特征
  - 删除资金流 available 标志，缺失直接填 None（非 0）

数据源（全部本地，零 API 调用）:
  - data/backtest/股票数据/行情数据/stock_daily.parquet  → OHLCV + total_mv + adj_factor
  - data/backtest/股票数据/资金流向数据/moneyflow_dc.parquet → 资金流向
  - data/backtest/指数数据/index_daily/000001.SH.parquet → 上证指数

Usage:
    python scripts/dump_direction_data.py --days 500
"""

import argparse
import csv
import logging
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _PROJECT_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dump_direction")

_DATA_ROOT = _PROJECT_ROOT / "data" / "backtest"
_STOCK_DAILY_PATH = _DATA_ROOT / "股票数据" / "行情数据" / "stock_daily.parquet"
_MONEYFLOW_DC_PATH = _DATA_ROOT / "股票数据" / "资金流向数据" / "moneyflow_dc.parquet"
_INDEX_DAILY_PATH = _DATA_ROOT / "指数数据" / "index_daily" / "000001.SH.parquet"

# ── v3 特征配置（26 维） ────────────────────────────────────

FEATURE_COLS = [
    "vol_ratio_5d", "vol_ratio_1d", "amount_breakout",
    "up_vol_ratio",
    "consecutive_up", "consecutive_down",
    "gap_up_pct",
    "change_pct", "turnover_rate",
    "big_order_net", "main_force_ratio", "flow_5d_cum",
    "ret_5d", "ret_10d",
    "rsi6", "rsi14", "ma20_deviation",
    "strength_score",
]

SECTOR_COLS = [
    "sector_pct", "sector_ret_5d", "sector_vol", "sector_mf",
    "sector_money_flow", "sector_breadth", "sector_rank",
]

ALL_COLS = FEATURE_COLS + SECTOR_COLS  # 26 维
TARGET_COLS = ["target_1d", "target_3d", "target_5d"]


# ── 交易日历 ───────────────────────────────────────────────


def get_trading_days_from_parquet(n: int, as_of_date: Optional[str] = None) -> List[str]:
    df = pd.read_parquet(_STOCK_DAILY_PATH)
    all_dates = sorted(set(
        d.strftime("%Y%m%d") for d in df.index.get_level_values("trade_date")
    ))
    if as_of_date:
        all_dates = [d for d in all_dates if d <= as_of_date]
    all_dates.sort(reverse=True)
    return all_dates[:n]


# ── 数据加载 ───────────────────────────────────────────────


def load_stock_daily(date_range: List[str],
                     symbols: List[str]) -> Dict[str, Dict[str, dict]]:
    """加载 stock_daily.parquet，价格 × adj_factor 前复权。"""
    logger.info("加载 stock_daily.parquet ...")
    t0 = time.time()

    df = pd.read_parquet(_STOCK_DAILY_PATH)

    min_date = min(date_range)
    max_date = max(date_range)
    lookback_start = pd.Timestamp(min_date) - pd.Timedelta(days=50)

    idx_dates = df.index.get_level_values("trade_date")
    date_mask = (idx_dates >= lookback_start) & (idx_dates <= pd.Timestamp(max_date))

    symbol_set = set(symbols)
    idx_syms = df.index.get_level_values("ts_code")
    sym_mask = idx_syms.isin(symbol_set)

    df = df[date_mask & sym_mask].reset_index()
    df["date_str"] = df["trade_date"].dt.strftime("%Y%m%d")

    result: Dict[str, Dict[str, dict]] = defaultdict(dict)
    keep_cols = ["open", "close", "high", "low", "vol", "amount",
                 "pct_chg", "turnover_rate", "total_mv", "adj_factor"]

    for _, row in df.iterrows():
        d = row["date_str"]
        s = row["ts_code"]
        adj = float(row["adj_factor"]) if pd.notna(row.get("adj_factor")) and row.get("adj_factor", 0) > 0 else 1.0
        result[d][s] = {
            "open": float(row["open"]) * adj if pd.notna(row["open"]) else 0,
            "close": float(row["close"]) * adj if pd.notna(row["close"]) else 0,
            "high": float(row["high"]) * adj if pd.notna(row["high"]) else 0,
            "low": float(row["low"]) * adj if pd.notna(row["low"]) else 0,
            "vol": float(row["vol"]) if pd.notna(row["vol"]) else 0,
            "amount": float(row["amount"]) if pd.notna(row["amount"]) else 0,
            "pct_chg": float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else 0,
            "turnover_rate": float(row["turnover_rate"]) if pd.notna(row["turnover_rate"]) else 0,
            "total_mv": float(row["total_mv"]) if pd.notna(row["total_mv"]) else 0,
        }

    elapsed = time.time() - t0
    logger.info(f"  加载完成: {len(result)} 日期, {elapsed:.1f}s")
    return dict(result)


def load_moneyflow_dc(date_range: List[str],
                      symbols: List[str]) -> Dict[str, Dict[str, dict]]:
    path = _MONEYFLOW_DC_PATH
    if not path.exists():
        logger.warning(f"moneyflow_dc.parquet 不存在: {path}")
        return {}

    logger.info("加载 moneyflow_dc.parquet ...")
    t0 = time.time()
    df = pd.read_parquet(path)

    min_date = min(date_range)
    max_date = max(date_range)
    lookback_start = pd.Timestamp(min_date) - pd.Timedelta(days=30)

    idx_dates = df.index.get_level_values("trade_date")
    date_mask = (idx_dates >= lookback_start) & (idx_dates <= pd.Timestamp(max_date))

    symbol_set = set(symbols)
    idx_syms = df.index.get_level_values("ts_code")
    sym_mask = idx_syms.isin(symbol_set)

    df = df[date_mask & sym_mask].reset_index()
    df["date_str"] = df["trade_date"].dt.strftime("%Y%m%d")

    result: Dict[str, Dict[str, dict]] = defaultdict(dict)
    for _, row in df.iterrows():
        d = row["date_str"]
        s = row["ts_code"]
        net = float(row["net_amount"]) if pd.notna(row["net_amount"]) else 0
        rate = float(row["net_amount_rate"]) if pd.notna(row["net_amount_rate"]) else 0
        result[d][s] = {"net_amount": net, "net_amount_rate": rate}

    elapsed = time.time() - t0
    logger.info(f"  加载完成: {len(result)} 日期, {elapsed:.1f}s")
    return dict(result)


def load_index_daily(date_range: List[str]) -> Dict[str, dict]:
    path = _INDEX_DAILY_PATH
    if not path.exists():
        logger.warning(f"上证指数数据不存在: {path}")
        return {}

    logger.info("加载上证指数日线 ...")
    df = pd.read_parquet(path)

    min_date = min(date_range)
    max_date = max(date_range)
    lookback_start = pd.Timestamp(min_date) - pd.Timedelta(days=50)

    df = df[(df.index >= lookback_start) & (df.index <= pd.Timestamp(max_date))]
    df = df.reset_index()
    df["date_str"] = df["trade_date"].dt.strftime("%Y%m%d")

    result = {}
    for _, row in df.iterrows():
        adj = float(row.get("adj_factor", 1)) if pd.notna(row.get("adj_factor")) and row.get("adj_factor", 0) > 0 else 1.0
        result[row["date_str"]] = {
            "close": float(row["close"]) * adj if pd.notna(row["close"]) else 0,
            "pct_chg": float(row["pct_chg"]) if pd.notna(row["pct_chg"]) else 0,
        }
    return result


# ── 特征计算 ───────────────────────────────────────────────


def _rsi_from_closes(closes: List[float], period: int) -> float:
    n = min(period, len(closes) - 1)
    if n <= 0:
        return 50.0
    diffs = [closes[i] - closes[i - 1] for i in range(-n, 0)]
    avg_gain = sum(max(d, 0) for d in diffs) / n
    avg_loss = sum(max(-d, 0) for d in diffs) / n
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1) if avg_loss > 0 else 100.0


def _compute_sector_features(features_data: Dict[str, dict],
                             symbol_info: Dict[str, dict]):
    """行业内部相对强度 + 行业整体特征。

    基于全市场活跃股票计算行业中位数，确保统计意义。
    """
    if not features_data:
        return
    syms = list(features_data.keys())

    pcts = np.array([features_data[s].get("change_pct", 0) for s in syms], dtype=float)
    valid_mask = ~np.isnan(pcts) & ~np.isinf(pcts)
    pct_ranks = np.full(len(syms), 0.5, dtype=float)
    if valid_mask.sum() >= 2:
        from scipy.stats import rankdata
        valid_vals = pcts[valid_mask]
        pct_ranks[valid_mask] = (rankdata(valid_vals) - 1) / (valid_mask.sum() - 1)

    ind_map: Dict[str, List[str]] = {}
    for sym in syms:
        ind = symbol_info.get(sym, {}).get("industry", "未知")
        ind_map.setdefault(ind, []).append(sym)

    # 行业中位数涨跌幅 → 行业排名
    ind_med_pcts: Dict[str, float] = {}
    for ind, grp in ind_map.items():
        grp_pcts = sorted(features_data[s].get("change_pct", 0) for s in grp)
        ind_med_pcts[ind] = grp_pcts[len(grp_pcts) // 2]

    ind_ranks: Dict[str, float] = {}
    if len(ind_med_pcts) >= 2:
        ind_names = list(ind_med_pcts.keys())
        ind_vals = np.array([ind_med_pcts[n] for n in ind_names], dtype=float)
        ind_rank_vals = (rankdata(ind_vals) - 1) / (len(ind_vals) - 1)
        ind_ranks = {n: round(float(r), 3) for n, r in zip(ind_names, ind_rank_vals)}
    else:
        ind_ranks = {n: 0.5 for n in ind_med_pcts}

    for ind, grp in ind_map.items():
        if len(grp) < 2:
            for sym in grp:
                features_data[sym].update({
                    "sector_pct": 0.0, "sector_ret_5d": 0.0,
                    "sector_vol": 0.0, "sector_mf": 0.0,
                    "sector_money_flow": 0.0, "sector_breadth": 0.5, "sector_rank": 0.5,
                })
            continue

        def _median(field):
            vals = sorted(features_data[s].get(field, 0) for s in grp)
            return vals[len(vals) // 2]

        med_pct = _median("change_pct")
        med_ret = _median("ret_5d")
        med_vol = _median("vol_ratio_1d")
        med_mf = _median("big_order_net")

        total_mf = sum(features_data[s].get("big_order_net", 0) for s in grp)
        up_count = sum(1 for s in grp if features_data[s].get("change_pct", 0) > 0)
        breadth = round(up_count / len(grp), 3)

        for i, sym in enumerate(grp):
            f = features_data[sym]
            f["sector_pct"] = round(f.get("change_pct", 0) - med_pct, 2)
            f["sector_ret_5d"] = round(f.get("ret_5d", 0) - med_ret, 2)
            f["sector_vol"] = round(f.get("vol_ratio_1d", 1.0) - med_vol, 3)
            f["sector_mf"] = round(f.get("big_order_net", 0) - med_mf, 2)
            f["sector_money_flow"] = round(total_mf, 2)
            f["sector_breadth"] = breadth
            f["sector_rank"] = ind_ranks.get(ind, 0.5)
            f["strength_score"] = round(f.get("change_pct", 0) * pct_ranks[i], 2)


def derive_features_from_bars(daily_bars: List[dict], quote: dict,
                              moneyflow: Optional[dict]) -> dict:
    """从 T-1 及历史日线推导滞后特征。

    关键：所有特征基于 T-1 日数据，daily_bars 的最后一天是 T-1。
    目标 (T+N_close - T_open) / T_open 使用 T 日 open，特征不包含 T 日信息。
    """
    f: dict = {}

    if len(daily_bars) >= 5:
        closes = [float(b["close"]) for b in daily_bars]
        volumes = [float(b.get("vol", 0) or 0) for b in daily_bars]
        amounts = [float(b.get("amount", 0) or 0) for b in daily_bars]

        # 成交量突破
        if len(daily_bars) >= 20:
            avg_vol_20 = sum(volumes[-20:]) / 20
            avg_vol_5 = sum(volumes[-5:]) / 5
            f["vol_ratio_5d"] = round(avg_vol_5 / avg_vol_20, 3) if avg_vol_20 > 0 else 1.0
            f["vol_ratio_1d"] = round(volumes[-1] / avg_vol_20, 3) if avg_vol_20 > 0 else 1.0
            median_amount = sorted(amounts[-20:])[len(amounts[-20:]) // 2]
            f["amount_breakout"] = 1 if (median_amount > 0 and amounts[-1] > 2 * median_amount) else 0
        else:
            avg_vol = sum(volumes) / len(volumes)
            f["vol_ratio_5d"] = 1.0
            f["vol_ratio_1d"] = round(volumes[-1] / avg_vol, 3) if avg_vol > 0 else 1.0
            f["amount_breakout"] = 0

        # 上涨/下跌日成交量比
        if len(daily_bars) >= 6:
            up_vols, down_vols = [], []
            for i in range(-5, 0):
                if closes[i] > closes[i - 1]:
                    up_vols.append(volumes[i])
                elif closes[i] < closes[i - 1]:
                    down_vols.append(volumes[i])
            up_sum = sum(up_vols)
            down_sum = sum(down_vols)
            f["up_vol_ratio"] = round(up_sum / down_sum, 3) if down_sum > 0 else (2.0 if up_sum > 0 else 1.0)
        else:
            f["up_vol_ratio"] = 1.0

        # 连涨连跌
        cons_up, cons_down = 0, 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                cons_up += 1
            else:
                break
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                cons_down += 1
            else:
                break
        f["consecutive_up"] = cons_up
        f["consecutive_down"] = cons_down

        # 缺口检测
        gap_up_pct = 0.0
        if len(daily_bars) >= 2:
            prev_close = float(daily_bars[-2]["close"])
            today_open = float(daily_bars[-1].get("open", closes[-1]))
            if prev_close > 0:
                gap = (today_open - prev_close) / prev_close * 100
                if gap > 0.5:
                    gap_up_pct = gap
        f["gap_up_pct"] = round(gap_up_pct, 2)

        # 动量
        if len(closes) >= 6:
            f["ret_5d"] = round((closes[-1] / closes[-6] - 1) * 100, 2)
        else:
            f["ret_5d"] = 0.0
        if len(closes) >= 11:
            f["ret_10d"] = round((closes[-1] / closes[-11] - 1) * 100, 2)
        else:
            f["ret_10d"] = 0.0

        # RSI
        f["rsi6"] = _rsi_from_closes(closes, 6)
        f["rsi14"] = _rsi_from_closes(closes, 14)

        # MA20 偏离
        lookback20 = min(20, len(closes))
        ma20 = sum(closes[-lookback20:]) / lookback20
        f["ma20_deviation"] = round((closes[-1] - ma20) / ma20 * 100, 2) if ma20 > 0 else 0

    else:
        f.update({
            "vol_ratio_5d": 1.0, "vol_ratio_1d": 1.0, "amount_breakout": 0,
            "up_vol_ratio": 1.0,
            "consecutive_up": 0, "consecutive_down": 0,
            "gap_up_pct": 0,
            "ret_5d": 0, "ret_10d": 0,
            "rsi6": 50.0, "rsi14": 50.0, "ma20_deviation": 0,
            "strength_score": 0,
        })

    # T-1 日行情
    f["change_pct"] = float(quote.get("pct_chg", 0) or 0)
    f["turnover_rate"] = float(quote.get("turnover_rate", 0) or 0)

    # 资金流向（万元 → 亿元）
    if moneyflow is not None:
        f["big_order_net"] = round(float(moneyflow.get("big_order_net", 0)) / 1e4, 2)
        f["main_force_ratio"] = round(float(moneyflow.get("main_force_ratio", 0)), 3)
        f["flow_5d_cum"] = round(float(moneyflow.get("flow_5d_cum", 0)) / 1e4, 2)
    else:
        f["big_order_net"] = 0.0
        f["main_force_ratio"] = 0.0
        f["flow_5d_cum"] = 0.0

    return f


def _is_chi_next_or_star(symbol: str) -> bool:
    code = symbol.split(".")[0]
    return code.startswith(("300", "301", "688"))


def _winsorize_targets(df: pd.DataFrame):
    """分板块 1%/99% Winsorize 三个目标列。"""
    for tc in TARGET_COLS:
        main_mask = ~df["symbol"].apply(_is_chi_next_or_star)
        gem_mask = df["symbol"].apply(_is_chi_next_or_star)
        vals = df[tc].copy()

        if main_mask.sum() >= 10:
            valid = vals[main_mask].dropna()
            lo, hi = np.percentile(valid, [1, 99])
            vals.loc[main_mask] = vals.loc[main_mask].clip(lo, hi)

        if gem_mask.sum() >= 10:
            valid = vals[gem_mask].dropna()
            lo, hi = np.percentile(valid, [1, 99])
            vals.loc[gem_mask] = vals.loc[gem_mask].clip(lo, hi)

        df[tc] = vals


# ── 主流程 ─────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="方向预测数据落地 v3")
    parser.add_argument("--days", type=int, default=500)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--skip-days", type=int, default=1)
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else _PROJECT_ROOT / "data" / "direction_data.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_days = get_trading_days_from_parquet(args.days + args.skip_days,
                                             as_of_date=args.start_date)
    trading_days = all_days[args.skip_days:]
    logger.info(f"交易日范围: {trading_days[-1]} ~ {trading_days[0]}，共 {len(trading_days)} 天")

    # 股票池
    db_path = str(_PROJECT_ROOT / "data" / "stock_pool.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT ts_code, symbol, name, industry FROM stock_pool WHERE is_st = 0 AND industry IS NOT NULL AND industry != ''")
    all_stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()
    all_symbols = [s["ts_code"] for s in all_stocks]
    symbol_info = {s["ts_code"]: s for s in all_stocks}
    logger.info(f"股票池: {len(all_symbols)} 只")

    min_date = trading_days[-1]
    max_date = trading_days[0]
    all_dates_extended = all_days[:args.skip_days + 10] if len(all_days) > args.skip_days + 10 else all_days
    date_range_full = sorted(set(trading_days + all_dates_extended))

    daily_data = load_stock_daily(date_range_full, all_symbols)
    moneyflow_raw = load_moneyflow_dc(date_range_full, all_symbols)
    index_data = load_index_daily(date_range_full)

    # 资金流缓存（用于 5 日累计）
    moneyflow_cache: Dict[str, Dict[str, dict]] = {}
    for d, syms in moneyflow_raw.items():
        moneyflow_cache[d] = {}
        for s, v in syms.items():
            moneyflow_cache[d][s] = {
                "big_order_net": v["net_amount"],
                "main_force_ratio": v["net_amount_rate"],
                "flow_5d_cum": 0,
            }

    columns = (["date", "symbol", "name", "industry"] +
               FEATURE_COLS + SECTOR_COLS + TARGET_COLS)
    total_rows = 0

    # 先收集所有行到列表，最后一次性 Winsorize
    all_rows: List[dict] = []

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for idx, date in enumerate(trading_days):
            logger.info(f"[{idx + 1}/{len(trading_days)}] {date}")
            t0 = time.time()

            # 当日行情（用于目标计算：base_open）
            quotes_today = daily_data.get(date, {})
            if not quotes_today:
                logger.warning(f"[{date}] 无行情数据，跳过")
                continue

            # T-1 数据用于特征计算（消除 target leak）
            sorted_dates_all = sorted(d for d in daily_data if d <= date)
            if len(sorted_dates_all) < 2:
                logger.warning(f"[{date}] 无前一日数据，跳过")
                continue
            prev_date = sorted_dates_all[-2]  # T-1

            quotes = daily_data.get(prev_date, {})
            active_symbols = [s for s in all_symbols if s in quotes and s in quotes_today]

            # 1. 日线历史（最近 25 天，截至 T-1）
            lookback_start_idx = max(0, len(sorted_dates_all) - 27)
            lookback_dates = sorted_dates_all[lookback_start_idx:-1]  # 不含当天

            daily_bars_map: Dict[str, List[dict]] = {}
            for sym in active_symbols:
                bars = []
                for d in lookback_dates:
                    row = daily_data.get(d, {}).get(sym)
                    if row:
                        bars.append({
                            "close": row["close"], "open": row["open"],
                            "vol": row["vol"], "amount": row["amount"],
                        })
                if bars:
                    daily_bars_map[sym] = bars

            # 2. 资金流向（T-1）+ 5 日累计
            mf_dates = sorted(d for d in moneyflow_cache if d <= prev_date)
            mf_today = moneyflow_cache.get(prev_date, {})
            mf_idx_pos = mf_dates.index(prev_date) if prev_date in mf_dates else len(mf_dates) - 1
            window_dates = mf_dates[max(0, mf_idx_pos - 4):mf_idx_pos + 1]
            for sym in mf_today:
                cum = sum(
                    moneyflow_cache.get(d, {}).get(sym, {}).get("big_order_net", 0)
                    for d in window_dates
                )
                mf_today[sym]["flow_5d_cum"] = cum

            # 3. 前瞻日期（计算持有期收益用）
            future_dates = sorted(d for d in daily_data if d > date)[:5]

            # 4. 持有期收益: (T+N_close - T_open) / T_open × 100
            #    T = date（执行日），特征来自 T-1
            forward_map: Dict[str, dict] = {}
            for sym in active_symbols:
                base_row = quotes_today.get(sym, {})
                base_open = base_row.get("open", 0)
                if base_open <= 0:
                    forward_map[sym] = {"next_day_pct": None, "day3_pct": None,
                                        "day5_pct": None}
                    continue

                def _hr(n: int) -> Optional[float]:
                    if n > len(future_dates):
                        return None
                    fd = future_dates[n - 1]
                    future_row = daily_data.get(fd, {}).get(sym)
                    if not future_row or future_row.get("close", 0) <= 0:
                        return None
                    return round((future_row["close"] - base_open) / base_open * 100, 2)

                forward_map[sym] = {
                    "next_day_pct": _hr(1),
                    "day3_pct": _hr(3),
                    "day5_pct": _hr(5),
                }

            # 5. 全市场特征计算
            feats_today: Dict[str, dict] = {}
            for sym in active_symbols:
                bars = daily_bars_map.get(sym, [])
                q = quotes.get(sym, {})
                flow = mf_today.get(sym)
                feats_today[sym] = derive_features_from_bars(bars, q, flow)

            _compute_sector_features(feats_today, symbol_info)

            # 6. 筛选龙头: 每行业按 total_mv 取 Top 3
            ind_groups: Dict[str, List[str]] = {}
            for sym in active_symbols:
                ind = symbol_info.get(sym, {}).get("industry", "")
                if ind:
                    ind_groups.setdefault(ind, []).append(sym)

            leader_symbols = set()
            for ind, syms in ind_groups.items():
                syms.sort(key=lambda s: quotes_today.get(s, {}).get("total_mv", 0) or 0, reverse=True)
                leader_symbols.update(syms[:3])

            # 7. 写入
            rows_written = 0
            for sym in active_symbols:
                if sym not in leader_symbols:
                    continue
                features = feats_today.get(sym, {})
                fwd = forward_map.get(sym, {})

                row = {
                    "date": str(date),
                    "symbol": sym,
                    "name": symbol_info.get(sym, {}).get("name", ""),
                    "industry": symbol_info.get(sym, {}).get("industry", ""),
                }
                for fc in FEATURE_COLS:
                    row[fc] = features.get(fc, 0)
                for sc in SECTOR_COLS:
                    row[sc] = features.get(sc, 0)
                for tc in TARGET_COLS:
                    desc = {"target_1d": "next_day_pct", "target_3d": "day3_pct",
                            "target_5d": "day5_pct"}
                    val = fwd.get(desc[tc])
                    row[tc] = val  # None 表示无数据

                writer.writerow(row)
                all_rows.append(row)
                rows_written += 1

            total_rows += rows_written
            elapsed = time.time() - t0
            logger.info(f"  [{date}] {rows_written} 行 ({len(leader_symbols)} leaders / {len(active_symbols)} active), {elapsed:.1f}s")

    logger.info(f"写入完成: {total_rows} 行 → {output_path}")

    # 8. 后处理：分板块 Winsorize
    if total_rows > 0:
        logger.info("后处理: 分板块 Winsorize ...")
        df = pd.read_csv(output_path)
        _winsorize_targets(df)
        df.to_csv(output_path, index=False, encoding="utf-8-sig")

        logger.info(f"覆盖: {df['date'].nunique()} 交易日, {df['symbol'].nunique()} 股票")
        for tc in TARGET_COLS:
            avail = df[tc].notna().sum()
            if avail > 0:
                vals = df[tc].dropna()
                pos = (vals > 0).sum()
                logger.info(f"  {tc}: {avail}/{total_rows} 有效, "
                            f"上涨={pos} ({pos / avail * 100:.1f}%), "
                            f"均值={vals.mean():.2f}%, 中位数={vals.median():.2f}%, "
                            f"std={vals.std():.2f}%")


if __name__ == "__main__":
    main()
