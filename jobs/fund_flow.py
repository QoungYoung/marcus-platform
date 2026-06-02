#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
资金流向综合分析模块
═══════════════════════════════════════════════════════
数据源: Tushare pro.moneyflow() + pro.limit_list()
用途:  供 market_scan.py 的 adjust_strategy() Step 8 使用

调用方传入关心的股票代码列表（watchlist + 持仓），按需拉取个股资金流：
  fund_flow = get_fund_flow_summary(symbols=['SH600570', 'SZ002230', ...])

输出结构:
{
    'market':  {'main_net_fmt': '+3.52亿'},
    'north':   {'total_net': 0, 'sh_net': 0, 'sz_net': 0},
    'limit_up':{'zt_count': 47, 'market_heat': 68},  ← 精确来自 limit_list
    'fund_score': 62.5,        # 0-100 综合评分
    'fund_signal': '温和流入',  # 信号标签
    'top_inflow': [
        {'industry': '半导体', 'net_fmt': '12.3亿', ...},
    ],
}
"""

import sys
import time
from pathlib import Path

# 确保能 import core/_api_config.py
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

import json
import pandas as pd
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

from _api_config import get_tushare_pro

# ═══════════════════════════════════════════════════════
# 内部工具
# ═══════════════════════════════════════════════════════

def _symbol_to_ts_code(symbol: str) -> str:
    """
    将股票代码转换为 tushare 标准格式 (xxxxxx.SH / xxxxxx.SZ)。
    兼容 SH600570 / SZ002230 / 600570 / 002230。
    """
    symbol = symbol.strip().upper()
    if symbol.startswith("SH"):
        return f"{symbol[2:]}.SH"
    elif symbol.startswith("SZ"):
        return f"{symbol[2:]}.SZ"
    elif symbol.startswith("6"):
        return f"{symbol}.SH"
    elif symbol.startswith(("0", "3")):
        return f"{symbol}.SZ"
    return symbol


def _lookup_industry(symbol: str) -> str:
    """从 stock_pool.db 查股票行业分类，查不到返回 '其他'"""
    try:
        import sqlite3
        db = Path(__file__).parent.parent / "data" / "stock_pool.db"
        if not db.exists():
            return "其他"
        conn = sqlite3.connect(str(db))
        short = symbol[2:] if symbol.startswith(("SH", "SZ")) else symbol
        row = conn.execute(
            "SELECT industry FROM stock_pool WHERE symbol = ? OR symbol = ? LIMIT 1",
            (symbol, short)
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return "其他"


def _recent_trade_date() -> str:
    """获取最近一个交易日（YYYYMMDD），跳过周末"""
    today = datetime.now()
    if today.weekday() == 5:        # 周六 → 回退到周五
        today -= timedelta(days=1)
    elif today.weekday() == 6:      # 周日 → 回退到周五
        today -= timedelta(days=2)
    return today.strftime("%Y%m%d")


def _fetch_one_moneyflow(pro, ts_code: str, trade_date: str):
    """
    获取单只股票当日的资金流向。
    返回 None 表示失败或该股今日无数据。
    """
    try:
        df = pro.moneyflow(
            ts_code=ts_code,
            start_date=trade_date,
            end_date=trade_date,
            limit=1,
        )
        if df is None or df.empty:
            return None

        row = df.iloc[0]

        # 主力 = 大单(20-100万) + 特大单(≥100万)
        def _f(col):
            return float(row.get(col, 0) or 0)

        main_buy = _f("buy_lg_amount") + _f("buy_elg_amount")
        main_sell = _f("sell_lg_amount") + _f("sell_elg_amount")
        # 总成交额（四类单合计）
        total = (main_buy + main_sell +
                 _f("buy_md_amount") + _f("sell_md_amount") +
                 _f("buy_sm_amount") + _f("sell_sm_amount"))

        return {
            "ts_code": ts_code,
            "main_buy": main_buy,          # 万元
            "main_sell": main_sell,        # 万元
            "main_net": main_buy - main_sell,
            "total_amount": total,
            "net_mf_amount": _f("net_mf_amount"),
        }
    except Exception as e:
        print(f"[fund_flow] ⚠️ {ts_code} 获取失败: {e}", file=sys.stderr)
        return None


def _fetch_limit_up(trade_date: str) -> dict:
    """
    获取当日涨停数据（实时，非估算）。
    数据源: akshare stock_zt_pool_em() → 东方财富实时涨停池

    Returns:
        {"zt_count": int, "dt_count": int, "market_heat": int, "top_industries": list} 或 None
    """
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=trade_date)
        if df is None or df.empty:
            return None

        zt_count = len(df)
        # 连板统计
        multi_board = len(df[df.get('连板数', 0) > 1]) if '连板数' in df.columns else 0
        # 行业分布 Top 5
        top_industries = []
        if '所属行业' in df.columns:
            industry_counts = df['所属行业'].value_counts().head(5)
            top_industries = [f"{ind}({cnt}只)" for ind, cnt in industry_counts.items()]

        # 市场热度
        if zt_count >= 100:
            heat = min(100, 70 + (zt_count - 100) * 0.3)
        elif zt_count >= 50:
            heat = 50 + (zt_count - 50) * 0.4
        elif zt_count >= 20:
            heat = 30 + (zt_count - 20) * 0.67
        else:
            heat = max(10, zt_count * 1.5)

        return {
            "zt_count": zt_count,
            "dt_count": 0,  # akshare 涨停池只含涨停，跌停需另查
            "market_heat": round(heat),
            "multi_board": multi_board,
            "top_industries": top_industries,
        }
    except Exception as e:
        print(f"[fund_flow] ⚠️ akshare 涨停池获取失败: {e}", file=sys.stderr)
        return None


# ═══════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════

def get_fund_flow_summary(symbols: list = None) -> dict:
    """
    获取指定股票的资金流向综合摘要。

    调用方传入关心的股票代码（watchlist + 持仓），按需拉取个股资金流，
    聚合计算主力净流向、综合评分、信号标签、板块排行。

    Args:
        symbols: 股票代码列表，如 ['SH600570', 'SZ002230', 'SH688981', ...]
                 每个代码独立查询 pro.moneyflow()，汇总后计算评分。

    Returns:
        dict: 见模块顶部文档
    """
    empty = {
        "market": {"main_net_fmt": "N/A"},
        "north": {"total_net": 0, "sh_net": 0, "sz_net": 0},
        "limit_up": {"zt_count": 0, "market_heat": 50},
        "fund_score": 50.0,
        "fund_signal": "中性",
        "top_inflow": [],
    }

    # ── 0. 初始化 Tushare（无论有无 symbols 都需要拉涨停数据）──
    try:
        pro = get_tushare_pro()
    except Exception as e:
        print(f"[fund_flow] ⚠️ Tushare 初始化失败: {e}", file=sys.stderr)
        return empty

    trade_date = _recent_trade_date()

    # ── 0.5 全市场涨停数据（akshare 实时，独立查询，不依赖 symbols）──
    limit_data = _fetch_limit_up(trade_date)
    if limit_data:
        empty["limit_up"] = {
            "zt_count": limit_data["zt_count"],
            "dt_count": limit_data.get("dt_count", 0),
            "market_heat": limit_data["market_heat"],
            "multi_board": limit_data.get("multi_board", 0),
            "top_industries": limit_data.get("top_industries", []),
        }
        print(f"[fund_flow] ✅ 全市场涨停: {limit_data['zt_count']}家 | 连板: {limit_data.get('multi_board', 0)}只 | 热度: {limit_data['market_heat']}", file=sys.stderr)
        if limit_data.get('top_industries'):
            print(f"[fund_flow]    热门行业: {', '.join(limit_data['top_industries'][:3])}", file=sys.stderr)
    else:
        print(f"[fund_flow] ⚠️ akshare 涨停池无数据", file=sys.stderr)

    if not symbols:
        print("[fund_flow] ⚠️ symbols 为空，跳过个股资金流查询", file=sys.stderr)
        return empty

    # 去重
    symbols = list(dict.fromkeys([s.strip().upper() for s in symbols]))

    # ── 2. 并发拉取个股资金流 ──
    def _batch_fetch(date_str: str) -> list[dict]:
        results = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            task_map = {
                pool.submit(_fetch_one_moneyflow, pro, _symbol_to_ts_code(sym), date_str): sym
                for sym in symbols
            }
            try:
                for fut in as_completed(task_map, timeout=25):
                    data = fut.result()
                    sym = task_map[fut]
                    if data:
                        data["symbol"] = sym
                        data["sector"] = _lookup_industry(sym)
                        results.append(data)
            except TimeoutError:
                print("[fund_flow] ⚠️ 部分请求超时", file=sys.stderr)
        return results

    results_raw = _batch_fetch(trade_date)

    if not results_raw:
        # 降级：尝试前一个交易日
        prev_date = (datetime.strptime(trade_date, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        print(f"[fund_flow] 今日无数据，回退到 {prev_date}...", file=sys.stderr)
        results_raw = _batch_fetch(prev_date)

    if not results_raw:
        print("[fund_flow] ⚠️ 无任何有效数据，返回空摘要", file=sys.stderr)
        return empty

    success_rate = len(results_raw) / len(symbols) * 100
    print(f"[fund_flow] 数据: {len(results_raw)}/{len(symbols)} ({success_rate:.0f}%)", file=sys.stderr)

    # ── 3. 聚合计算 ──
    total_main_buy = sum(r["main_buy"] for r in results_raw)
    total_main_sell = sum(r["main_sell"] for r in results_raw)
    total_net = total_main_buy - total_main_sell               # 万元
    total_amount = sum(r["total_amount"] for r in results_raw)  # 万元

    # 主力净额（格式化）
    net_yi = total_net / 10000  # 万元 → 亿元
    if abs(net_yi) >= 1:
        main_net_fmt = f"{net_yi:+.2f}亿"
    else:
        main_net_fmt = f"{total_net:+.0f}万"

    # 主力净占比
    main_net_pct = (total_net / total_amount * 100) if total_amount > 0 else 0

    # ── 4. 综合评分 fund_score (0-100) ──
    # 映射：main_net_pct -10% → 0, 0% → 50, +10% → 100
    fund_score = 50 + main_net_pct * 5
    fund_score = round(max(0.0, min(100.0, fund_score)), 1)

    # ── 5. 信号标签 fund_signal ──
    if fund_score >= 70:
        fund_signal = "强势流入"
    elif fund_score >= 60:
        fund_signal = "温和流入"
    elif fund_score >= 45:
        fund_signal = "中性"
    elif fund_score >= 35:
        fund_signal = "温和流出"
    else:
        fund_signal = "强势流出"

    # ── 6. 板块资金排行 top_inflow ──
    sector_map: dict[str, dict] = {}
    for r in results_raw:
        s = r["sector"]
        if s not in sector_map:
            sector_map[s] = {"net": 0.0, "stocks": []}
        sector_map[s]["net"] += r["main_net"]
        sector_map[s]["stocks"].append(r.get("name", r.get("symbol", "?")))

    ranked = sorted(sector_map.items(), key=lambda x: -x[1]["net"])
    top_inflow = []
    for s_name, s_data in ranked[:5]:
        n = s_data["net"] / 10000
        fmt = f"{n:+.2f}亿" if abs(n) >= 1 else f"{s_data['net']:+.0f}万"
        top_inflow.append({
            "industry": s_name,
            "net_fmt": fmt,
            "lead_stock": s_data["stocks"][0] if s_data["stocks"] else "",
            "change_pct": 0.0,  # 需额外行情接口，暂不填充
        })

    # ── 7. 涨停 / 市场热度（精确查询） ──
    limit_data = _fetch_limit_up(trade_date)
    if limit_data:
        zt_count = limit_data["zt_count"]
        dt_count = limit_data["dt_count"]
        market_heat = limit_data["market_heat"]
    else:
        # 降级估算（limit_list 接口不可用或不返回数据时）
        if main_net_pct > 2:
            market_heat = min(90, 60 + main_net_pct * 10)
            zt_count = int(30 + main_net_pct * 10)
        elif main_net_pct > 0:
            market_heat = min(80, 50 + main_net_pct * 5)
            zt_count = int(20 + main_net_pct * 5)
        elif main_net_pct > -2:
            market_heat = max(30, 50 + main_net_pct * 5)
            zt_count = max(5, int(20 + main_net_pct * 5))
        else:
            market_heat = max(10, 40 + main_net_pct * 5)
            zt_count = max(1, int(15 + main_net_pct * 5))
        dt_count = 0

    limit_up = {"zt_count": zt_count, "market_heat": round(market_heat)}

    # ── 8. 组装结果 ──
    result = {
        "market": {"main_net_fmt": main_net_fmt},
        "north": {"total_net": 0, "sh_net": 0, "sz_net": 0},
        "limit_up": limit_up,
        "fund_score": fund_score,
        "fund_signal": fund_signal,
        "top_inflow": top_inflow,
    }

    print(f"[fund_flow] ✅ net={main_net_fmt} | score={fund_score} | "
          f"signal={fund_signal} | heat={market_heat} | zt={zt_count}", file=sys.stderr)

    return result


# ═══════════════════════════════════════════════════════
# 自测
# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    # 自测：传入示例股票代码
    summary = get_fund_flow_summary(
        symbols=["SH600570", "SZ002230", "SH688981", "SZ300750", "SH688256"]
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
