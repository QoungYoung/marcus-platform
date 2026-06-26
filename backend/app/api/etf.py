# -*- coding: utf-8 -*-
"""
ETF data API endpoints.
"""
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/etf", tags=["ETF Data"])

# Import paths from settings (same pattern as other API modules)
from app.config import get_settings
settings = get_settings()


def _get_xueqiu_engine():
    """Get XueqiuEngine instance"""
    sys_path = str(settings.xueqiu_dir)
    if sys_path not in __import__('sys').path:
        __import__('sys').path.insert(0, sys_path)
    from xueqiu_engine import XueqiuEngine
    return XueqiuEngine(config_file=str(settings.xueqiu_dir / "config.json"))


@router.post("/sync")
async def sync_etf_pool(pages: int = 5):
    """
    同步ETF板块池数据从雪球API到数据库
    """
    try:
        engine = _get_xueqiu_engine()
        count = engine.sync_etf_pool(pages=pages)
        return {"synced": count, "updated_at": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sync failed: {str(e)}")


@router.get("/list")
async def get_etf_list(sector: Optional[str] = None, limit: int = 100):
    """
    获取 ETF 板块池列表（从数据库查询）
    """
    try:
        engine = _get_xueqiu_engine()
        etf_list = engine.get_etf_pool_from_db(sector=sector, limit=limit)

        # 按 priority 分组
        sector_priority = {}
        for etf in etf_list:
            s = etf.get('sector', '未知')
            if s not in sector_priority:
                sector_priority[s] = []
            if etf.get('symbol') not in sector_priority[s]:
                sector_priority[s].append(etf.get('symbol'))

        return {
            "etf_list": etf_list,
            "sector_count": len(sector_priority),
            "total_count": len(etf_list),
            "updated_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ETF list: {str(e)}")


@router.get("/quotes")
async def get_etf_quotes(symbols: Optional[str] = None, top_n: int = 12):
    """
    获取 ETF 实时行情

    - 若不指定 symbols，则返回前 top_n 只 ETF 的行情
    - symbols 格式：逗号分隔的 ETF 代码列表，如 "SH512480,SZ159995"
    """
    try:
        engine = _get_xueqiu_engine()
        etf_list = engine.get_etf_pool_from_db(limit=200)

        if symbols:
            sym_list = [s.strip() for s in symbols.split(",")]
            target_etfs = [e for e in etf_list if e['symbol'] in sym_list]
        else:
            target_etfs = etf_list[:top_n]

        syms = [e['symbol'] for e in target_etfs]
        quotes = engine.batch_get_etf_quotes(syms)

        result = []
        for etf in target_etfs:
            sym = etf['symbol']
            q = quotes.get(sym, {})
            result.append({
                "symbol": sym,
                "name": etf.get("name", q.get("name", "")),
                "sector": etf.get("sector", ""),
                "current": q.get("current", 0),
                "percent": q.get("percent", 0),
                "amount": q.get("amount", 0),
                "volume": q.get("volume", 0),
                "turnover_rate_est": q.get("turnover_rate_est", 0),
                "last_close": q.get("last_close", 0),
                "high": q.get("high", 0),
                "low": q.get("low", 0),
            })

        return {
            "quotes": result,
            "count": len(result),
            "updated_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ETF quotes: {str(e)}")


@router.get("/candidates")
async def get_etf_candidates(
    top_n: int = 5,
    market_stance: str = "yellow",
    hot_sectors: Optional[str] = None,
):
    """
    获取 ETF 候选列表（三重确认选股）

    - top_n: 返回最多多少只
    - market_stance: 市场立场 (green/yellow/red)
    - hot_sectors: 外部热点行业，逗号分隔
    """
    try:
        engine = _get_xueqiu_engine()
        etf_list = engine.get_etf_pool_from_db(limit=200)

        # 默认优先级
        priority_map = {}
        p1 = ["半导体", "军工", "AI/算力", "AI", "科创", "科技"]
        p2 = ["新能源", "消费", "医疗", "通信", "券商", "互联网", "游戏"]
        for s in p1:
            priority_map[s] = 1
        for s in p2:
            priority_map[s] = 2

        min_amount = 100000000
        momentum_threshold = {
            "green": -1.0,
            "yellow": 0.5,
            "red": 1.5,
        }.get(market_stance, 0.5)

        hot_list = []
        if hot_sectors:
            hot_list = [s.strip() for s in hot_sectors.split(",")]
        hot_set = set(hot_list)

        syms = [e['symbol'] for e in etf_list]
        quotes = engine.batch_get_etf_quotes(syms)

        scored = []
        for etf in etf_list:
            sym = etf['symbol']
            q = quotes.get(sym, {})
            if not q or not q.get("current"):
                continue

            sector = etf.get("sector", "")
            amount = q.get("amount", 0)
            pct_1d = q.get("percent", 0) or 0

            #流动性过滤
            if amount < min_amount:
                continue

            # Momentum门槛
            if pct_1d < momentum_threshold:
                continue

            # 简化评分：Momentum 40% + 板块催化 60%
            momentum_score = max(0, min(50, (pct_1d + 2) * 12.5))
            sector_pri = priority_map.get(sector, 3)
            # 热点加成
            sector_score = 30 + (3 - sector_pri) * 10
            if sector in hot_set:
                sector_score += 10
            catalyst_score = sector_score * 0.6 + momentum_score * 0.4

            tag = '🟢' if catalyst_score >= 70 else ('🟡' if catalyst_score >= 55 else '🔴')

            scored.append({
                "symbol": sym,
                "name": etf.get("name", ""),
                "sector": sector,
                "catalyst_score": round(catalyst_score, 1),
                "catalyst_tag": tag,
                "pct_1d": pct_1d,
                "momentum_score": round(momentum_score, 1),
                "sector_news_score": round(sector_score, 1),
                "amount": amount,
                "_sector_pri": sector_pri,
            })

        # Sort by priority then score
        scored.sort(key=lambda x: (x["_sector_pri"], -x["catalyst_score"]))

        # Clean up internal field
        for c in scored:
            c.pop("_sector_pri", None)

        return {
            "candidates": scored[:top_n],
            "count": len(scored[:top_n]),
            "market_stance": market_stance,
            "hot_sectors": hot_list,
            "updated_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ETF candidates: {str(e)}")


@router.get("/sectors/hot")
async def get_hot_sectors(limit: int = 5):
    """
    获取热点板块（按 ETF 配置的优先级排序）
    """
    try:
        engine = _get_xueqiu_engine()
        etf_list = engine.get_etf_pool_from_db(limit=200)

        # 统计各板块ETF数量
        sector_count = {}
        for etf in etf_list:
            sector = etf.get('sector', '未知')
            sector_count[sector] = sector_count.get(sector, 0) + 1

        # 默认优先级
        p1 = ["半导体", "军工", "AI/算力", "AI", "科创", "科技"]
        p2 = ["新能源", "消费", "医疗", "通信", "券商", "互联网", "游戏"]

        hot = []
        for sector_list, pri in [(p1, 1), (p2, 2)]:
            for sector in sector_list:
                if sector in sector_count:
                    hot.append({
                        "sector": sector,
                        "priority": pri,
                        "etf_count": sector_count[sector],
                        "news_score": 50 - pri * 10 + 5,
                    })

        hot = hot[:limit]

        return {
            "hot_sectors": hot,
            "count": len(hot),
            "updated_at": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch hot sectors: {str(e)}")


@router.get("/quote/{symbol}")
async def get_single_etf_quote(symbol: str):
    """
    获取单只 ETF 实时行情
    """
    try:
        engine = _get_xueqiu_engine()
        quote = engine.get_etf_quote(symbol)

        if not quote:
            raise HTTPException(status_code=404, detail=f"ETF {symbol} not found")

        return {
            "symbol": symbol,
            "name": quote.get("name", ""),
            "current": quote.get("current", 0),
            "percent": quote.get("percent", 0),
            "amount": quote.get("amount", 0),
            "volume": quote.get("volume", 0),
            "turnover_rate_est": quote.get("turnover_rate_est", 0),
            "last_close": quote.get("last_close", 0),
            "high": quote.get("high", 0),
            "low": quote.get("low", 0),
            "updated_at": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ETF quote: {str(e)}")


@router.get("/detail/{symbol}")
async def get_etf_detail(symbol: str):
    """
    获取ETF详细信息
    """
    try:
        engine = _get_xueqiu_engine()
        detail = engine.get_etf_detail(symbol)

        if not detail:
            raise HTTPException(status_code=404, detail=f"ETF {symbol} not found")

        detail['updated_at'] = datetime.now().isoformat()
        return detail
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ETF detail: {str(e)}")


@router.get("/kline/{symbol}")
async def get_etf_kline(
    symbol: str,
    period: str = "day",
    count: int = -284,
    begin: Optional[int] = None
):
    """
    获取ETF K线数据

    - period: K线周期 (day 使用 Tushare fund_daily / 其他周期使用雪球)
    - count: 数据条数，负数表示取起点之前的历史数据
    - begin: 起始时间戳（毫秒）
    """
    try:
        # ── 日线：使用 Tushare fund_daily（更稳定、数据更完整） ──
        if period == "day":
            klines = _fetch_etf_kline_from_tushare(symbol, count=abs(count) if count < 0 else count)
        else:
            # 其他周期（周/月/分钟）仍使用雪球
            engine = _get_xueqiu_engine()
            klines = engine.get_etf_kline(symbol, period=period, count=count, begin=begin)

        if not klines:
            raise HTTPException(status_code=404, detail=f"No kline data for {symbol}")

        return {
            "symbol": symbol,
            "period": period,
            "count": len(klines),
            "klines": klines,
            "count": len(klines),
            "updated_at": datetime.now().isoformat(),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ETF kline: {str(e)}")


def _fetch_etf_kline_from_tushare(symbol: str, count: int = 284) -> list:
    """
    通过 Tushare fund_daily 接口获取 ETF 日线行情。

    Args:
        symbol: ETF 代码，如 "510050"、"SH510050"、"159513"
        count: 返回最近多少条数据

    Returns:
        [{"timestamp": "2026-06-26", "open": 1.234, "high": ..., "low": ..., 
          "close": ..., "volume": ..., "amount": ..., "change_pct": ...}, ...]
    """
    import tushare as ts
    from app.config import get_settings
    from datetime import datetime as dt, timedelta

    settings = get_settings()
    token = settings.get_tushare_token()
    pro = ts.pro_api(token)

    # 符号标准化：SH510050 / 510050 → 510050.SH
    ts_code = _symbol_to_ts_code(symbol, market='SH')

    # 计算日期范围
    end_date = dt.now().strftime("%Y%m%d")
    # 多取一些，留出非交易日余量（ETF fund_daily 只在交易日有数据）
    start_date = (dt.now() - timedelta(days=count * 2)).strftime("%Y%m%d")

    df = pro.fund_daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
    
    if df is None or df.empty:
        # 尝试深市
        ts_code_sz = _symbol_to_ts_code(symbol, market='SZ')
        if ts_code_sz != ts_code:
            df = pro.fund_daily(ts_code=ts_code_sz, start_date=start_date, end_date=end_date)

    if df is None or df.empty:
        return []

    df = df.sort_values("trade_date", ascending=True)
    klines = []
    for _, row in df.iterrows():
        klines.append({
            "timestamp": str(row["trade_date"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row["vol"]),
            "amount": float(row["amount"]) * 1000,  # Tushare fund_daily amount 单位千元→元
            "change_pct": float(row.get("pct_chg", 0)),
        })

    # 截取最近 count 条
    return klines[-count:] if len(klines) > count else klines


def _symbol_to_ts_code(symbol: str, market: str = 'SH') -> str:
    """
    将 ETF 代码标准化为 Tushare 格式。

    "SH510050" → "510050.SH"
    "510050" → "510050.SH" (默认沪市)
    "SZ159995" → "159995.SZ"
    "159995" → "159995.SZ" (判断：159开头→深市)
    """
    s = symbol.strip().upper()
    if s.startswith("SH"):
        return f"{s[2:]}.SH"
    if s.startswith("SZ"):
        return f"{s[2:]}.SZ"
    # 纯数字：按开头判断
    if s.startswith("159") or s.startswith("16"):
        return f"{s}.SZ"
    return f"{s}.SH"