# -*- coding: utf-8 -*-
"""落地脚本：遍历历史交易日，收集各维度得分 + 前瞻收益，导出为 CSV。

用法:
    cd backend && python ../scripts/dump_scores.py --days 60 --output ../data/scores_dump.csv

输出列:
    date, symbol, name, industry, market_cap, change_pct, market_regime,
    trend_score, volume_price_score, industry_relative_score,
    price_residual_score, overbought_score, capital_score, capital_data,
    next_day_pct, day3_pct, day5_pct
"""

import argparse
import csv
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set

# 确保 backend 在 sys.path 中，方便导入 service
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _PROJECT_ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# 日志
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("dump_scores")

# Tushare pro 单例
_tushare_pro = None


def _get_tushare_pro():
    global _tushare_pro
    if _tushare_pro is None:
        from app.core.trading._api_config import get_tushare_pro
        _tushare_pro = get_tushare_pro()
    return _tushare_pro


# ── 交易日历 ───────────────────────────────────────────────


def get_trading_days(n: int, as_of_date: Optional[str] = None) -> List[str]:
    """取最近 N 个交易日，倒序（最新在前）。"""
    pro = _get_tushare_pro()
    ref = datetime.strptime(as_of_date, "%Y%m%d") if as_of_date else datetime.now()
    end_date = ref.strftime("%Y%m%d")
    start_date = (ref - timedelta(days=n * 3)).strftime("%Y%m%d")
    df = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date)
    if df is not None and not df.empty:
        days = [str(d) for d in df[df['is_open'] == 1]['cal_date'].tolist()]
        days.sort(reverse=True)
        return days[:n]

    # 工作日回退
    cursor = ref
    days = []
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor.strftime("%Y%m%d"))
        cursor -= timedelta(days=1)
    return days


def get_future_trading_days(after_date: str, n: int = 10) -> List[str]:
    """获取 after_date 之后的 N 个交易日。"""
    pro = _get_tushare_pro()
    ref = datetime.strptime(after_date, "%Y%m%d")
    start = (ref + timedelta(days=1)).strftime("%Y%m%d")
    end = (ref + timedelta(days=n * 3)).strftime("%Y%m%d")
    df = pro.trade_cal(exchange='SSE', start_date=start, end_date=end)
    if df is not None and not df.empty:
        days = sorted(str(d) for d in df[df['is_open'] == 1]['cal_date'].tolist())
        return days[:n]
    return []


# ── 批量前瞻收益 ───────────────────────────────────────────


def batch_forward_returns(
    symbols: List[str], benchmark_date: str
) -> Dict[str, dict]:
    """批量计算前瞻收益。

    一次 daily 调用拉取所有 symbol 从 benchmark_date 起的数据，
    避免逐股调 Tushare API。

    返回: {symbol: {next_day_pct, day3_pct, day5_pct, available}}
    """
    result: Dict[str, dict] = {}
    empty = lambda warn: {
        "next_day_pct": None, "day3_pct": None, "day5_pct": None,
        "available": False, "warning": warn,
    }

    if not symbols:
        return result

    future_days = get_future_trading_days(benchmark_date, n=10)
    if len(future_days) < 1:
        for s in symbols:
            result[s] = empty("无后续交易日数据")
        return result

    pro = _get_tushare_pro()
    end_date = future_days[-1]

    try:
        # 批量拉取日线
        df = pro.daily(
            ts_code=",".join(symbols),
            start_date=benchmark_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            for s in symbols:
                result[s] = empty("无日线数据")
            return result

        df = df.sort_values("trade_date")
        grouped = df.groupby("ts_code")

        for sym in symbols:
            if sym not in grouped.groups:
                result[sym] = empty(f"{sym} 无日线数据")
                continue

            grp = grouped.get_group(sym)
            closed = dict(zip(grp["trade_date"].tolist(), grp["close"].tolist()))

            # 找基准日收盘价
            base_date = benchmark_date
            if base_date not in closed:
                prev = [d for d in closed if d <= base_date]
                if not prev:
                    result[sym] = empty("基准日之前无交易数据")
                    continue
                base_date = prev[-1]
            base_close = float(closed[base_date])

            def _ret(n: int) -> Optional[float]:
                if n > len(future_days):
                    return None
                td = future_days[n - 1]
                if td not in closed:
                    return None
                c = float(closed[td])
                return round((c - base_close) / base_close * 100, 2)

            result[sym] = {
                "next_day_pct": _ret(1),
                "day3_pct": _ret(3),
                "day5_pct": _ret(5),
                "available": True,
                "warning": "",
            }

    except Exception as e:
        logger.warning(f"batch_forward_returns 失败 ({benchmark_date}): {e}")
        for s in symbols:
            if s not in result:
                result[s] = empty(str(e))

    return result


# ── 主流程 ─────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="落地龙头排行评分 + 前瞻收益数据")
    parser.add_argument("--days", type=int, default=60, help="历史交易日数量 (默认 60)")
    parser.add_argument("--output", type=str, default=None, help="输出 CSV 路径")
    parser.add_argument("--start-date", type=str, default=None,
                        help="起始日期 YYYYMMDD（倒数 days 个交易日至此日期）")
    parser.add_argument("--skip-days", type=int, default=1,
                        help="跳过最近 N 个交易日（无前瞻数据），默认 1")
    parser.add_argument("--limit", type=int, default=30,
                        help="每个日期收录前 N 只股票 (默认 30, 0=全部候选股)")
    args = parser.parse_args()

    # 输出路径
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = _PROJECT_ROOT / "data" / "scores_dump.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 交易日列表
    all_days = get_trading_days(args.days + args.skip_days, as_of_date=args.start_date)
    trading_days = all_days[args.skip_days:]  # 跳过最近 N 天（无前瞻数据）
    logger.info(f"交易日范围: {trading_days[-1]} ~ {trading_days[0]}，共 {len(trading_days)} 天")

    # 初始化 service（同一实例，利用内存缓存）
    from app.services.industry_leaderboard import IndustryLeaderboardService
    service = IndustryLeaderboardService()

    # CSV 列
    columns = [
        "date", "symbol", "name", "industry", "market_cap", "change_pct",
        "market_regime",
        "trend_score", "valuation_score", "reversal_score",
        "overbought_score",
        "next_day_pct", "day3_pct", "day5_pct",
    ]

    total_rows = 0
    failed_dates: List[str] = []

    with open(output_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()

        for idx, date in enumerate(trading_days):
            logger.info(f"[{idx + 1}/{len(trading_days)}] {date} — 获取排行...")
            t0 = time.time()

            try:
                fetch_limit = args.limit if args.limit > 0 else 9999
                result = service.get_leaderboard(date=date, limit=fetch_limit, refresh=True)
            except Exception as e:
                logger.error(f"[{date}] get_leaderboard 失败: {e}")
                failed_dates.append(date)
                continue

            items = result.get("items", [])
            regime = result.get("market_regime", "transitional")
            if not items:
                logger.warning(f"[{date}] 无排行数据，跳过")
                continue

            # 批量前瞻收益
            symbols = [it["symbol"] for it in items]
            t1 = time.time()
            forward_map = batch_forward_returns(symbols, date)
            t2 = time.time()

            rows_written = 0
            for it in items:
                sym = it["symbol"]
                fwd = forward_map.get(sym, {})
                row = {
                    "date": str(date),
                    "symbol": sym,
                    "name": it.get("name", ""),
                    "industry": it.get("industry", ""),
                    "market_cap": it.get("market_cap", 0),
                    "change_pct": it.get("change_pct", 0),
                    "market_regime": regime,
                    "trend_score": it.get("trend_score", 0),
                    "valuation_score": it.get("valuation_score", 0),
                    "reversal_score": it.get("reversal_score", 0),
                    "overbought_score": it.get("overbought_score", 0),
                    "next_day_pct": fwd.get("next_day_pct"),
                    "day3_pct": fwd.get("day3_pct"),
                    "day5_pct": fwd.get("day5_pct"),
                }
                writer.writerow(row)
                rows_written += 1

            total_rows += rows_written
            elapsed = time.time() - t0
            logger.info(
                f"  [{date}] {rows_written} 行, "
                f"排行 {t1 - t0:.1f}s, 前瞻 {t2 - t1:.1f}s, 总计 {elapsed:.1f}s"
            )

    # 汇总
    logger.info(f"完成: {total_rows} 行 → {output_path}")
    if failed_dates:
        logger.warning(f"失败日期 ({len(failed_dates)}): {failed_dates}")

    # 简单统计
    if total_rows > 0:
        import pandas as pd
        df = pd.read_csv(output_path)
        n_dates = df["date"].nunique()
        n_symbols = df["symbol"].nunique()
        logger.info(f"覆盖: {n_dates} 个交易日, {n_symbols} 只股票")
        for col in ["next_day_pct", "day3_pct", "day5_pct"]:
            avail = df[col].notna().sum()
            logger.info(f"  {col}: {avail}/{total_rows} 有效 ({avail / total_rows * 100:.1f}%)")


if __name__ == "__main__":
    main()
