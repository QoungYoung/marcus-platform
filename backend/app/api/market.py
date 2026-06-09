# -*- coding: utf-8 -*-
"""
Market data API endpoints.
"""
import logging
import sys
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Query

from app.models.market import IndexResponse, IndicesResponse, QuoteResponse, SectorResponse, SectorsResponse, GlobalMarketResponse, GlobalIndexResponse, CommodityResponse, KlineData, KlineResponse, MoneyflowData, MoneyflowResponse, TechnicalData, TechnicalResponse, ProBarData, ProBarResponse

router = APIRouter(prefix="/market", tags=["Market Data"])

# Import XUEQIU_DIR from workspace_detector (core package)
from pathlib import Path
import sys as _sys
_platform_root = Path(__file__).parent.parent.parent
_core_dir = _platform_root / "core"
if str(_core_dir) not in _sys.path:
    _sys.path.insert(0, str(_core_dir))
from workspace_detector import XUEQIU_DIR

# 东财实时板块资金流（保留导入失败的降级处理）
try:
    from utils.em_sector_flow import (
        get_sector_flow, get_top_inflow_sectors, get_top_change_sectors,
        get_sector_flow_by_name, get_market_sector_summary,
        classify_flow_nature,
    )
    _EM_FLOW_AVAILABLE = True
    logger.info("[market] ✅ 东财实时板块资金流模块已加载")
except ImportError:
    _EM_FLOW_AVAILABLE = False
    logger.warning("[market] ⚠️ 东财实时板块资金流模块不可用")


@router.get("/indices", response_model=IndicesResponse)
async def get_market_indices():
    """
    Get major market indices.
    Data source: Xueqiu (Snowball) API.
    """
    try:
        from xueqiu_engine import XueqiuEngine

        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
        indices_symbols = ["SH000001", "SZ399001", "SH000300", "SZ399006", "SH000688"]
        indices_names = ["上证指数", "深证成指", "沪深300", "创业板指", "科创50"]

        indices = []
        for symbol, name in zip(indices_symbols, indices_names):
            try:
                quote = engine.get_stock_quote(symbol)
                indices.append(IndexResponse(
                    symbol=symbol,
                    name=name,
                    current_price=quote.get("current", 0),
                    last_close=quote.get("last_close", 0),
                    change=quote.get("chg", 0),
                    change_pct=quote.get("percent", 0),
                    volume=quote.get("volume", 0),
                    high=quote.get("high", 0),
                    low=quote.get("low", 0),
                    open_price=quote.get("open", 0),
                    gap_pct=0,  # Calculate from open vs last_close
                    updated_at=datetime.now(),
                ))
            except Exception:
                indices.append(IndexResponse(
                    symbol=symbol,
                    name=name,
                    current_price=0,
                    last_close=0,
                    change=0,
                    change_pct=0,
                    volume=0,
                    high=0,
                    low=0,
                    open_price=0,
                    gap_pct=0,
                    updated_at=datetime.now(),
                ))

        return IndicesResponse(indices=indices, updated_at=datetime.now())
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch indices: {str(e)}")


@router.get("/quote/{symbol}", response_model=QuoteResponse)
async def get_stock_quote(symbol: str):
    """
    Get real-time quote for a specific stock.
    Data source: Xueqiu (Snowball) API.
    """
    try:
        from xueqiu_engine import XueqiuEngine

        # Normalize symbol format
        if not symbol.startswith(("SH", "SZ", "BJ")):
            if symbol.startswith("6"):
                symbol = "SH" + symbol
            elif symbol.startswith(("0", "3")):
                symbol = "SZ" + symbol

        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
        quote = engine.get_stock_quote(symbol)

        return QuoteResponse(
            symbol=symbol,
            name=quote.get("name", ""),
            current=quote.get("current", 0),
            change=quote.get("chg", 0),
            percent=quote.get("percent", 0),
            last_close=quote.get("last_close", 0),
            open=quote.get("open"),
            high=quote.get("high"),
            low=quote.get("low"),
            volume=quote.get("volume", 0),
            amount=quote.get("amount", 0),
            turnover_rate=quote.get("turnover_rate"),
            amplitude=quote.get("amplitude"),
            pe_ttm=quote.get("pe_ttm"),
            pb=quote.get("pb"),
            market_capital=quote.get("market_capital"),
            float_market_capital=quote.get("float_market_capital"),
            avg_price=quote.get("avg_price"),
            high_52w=quote.get("high_52w"),
            low_52w=quote.get("low_52w"),
            updated_at=datetime.now(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch quote: {str(e)}")


@router.get("/global", response_model=GlobalMarketResponse)
async def get_global_market():
    """
    Get US indices and commodities (gold, crude oil).
    Data source: akshare (US indices, commodities) + xueqiu (A50 futures).
    Returns fallback data when markets are closed.
    """
    try:
        from core.utils.us_market_linkage import get_us_indices, get_commodities

        us_indices_raw = get_us_indices()
        commodities_raw = get_commodities()

        us_indices = []
        for name, data in us_indices_raw.items():
            us_indices.append(GlobalIndexResponse(
                name=name,
                symbol=data.get("symbol", ""),
                current=data.get("current", 0),
                change=data.get("change", 0),
                change_pct=data.get("change_pct", 0),
                update_time=data.get("update_time", ""),
            ))

        commodities = []
        for name, data in commodities_raw.items():
            commodities.append(CommodityResponse(
                name=name,
                symbol=data.get("symbol", ""),
                current=data.get("current", 0),
                change=data.get("change", 0),
                change_pct=data.get("change_pct", 0),
                update_time=data.get("update_time", ""),
            ))

        return GlobalMarketResponse(
            us_indices=us_indices,
            commodities=commodities,
            updated_at=datetime.now(),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch global market: {str(e)}")


# ========== 股票/ETF 搜索 API (用于 @mention) ==========

@router.get("/search")
async def search_market(q: str = Query(..., min_length=1, description="搜索关键词(代码或名称)")):
    """
    搜索股票和ETF，用于聊天 @提及 功能。
    股票从 stock_pool.db 动态查询，ETF 从 cache.db 查询。
    """
    import sqlite3
    from app.config import get_settings
    settings = get_settings()
    results = []
    q_lower = q.lower().strip()

    # 0. 市场指数（5个常用，不在 stock_pool 中）
    INDICES = [
        {"symbol": "SH000001", "name": "上证指数", "type": "index"},
        {"symbol": "SZ399001", "name": "深证成指", "type": "index"},
        {"symbol": "SH000300", "name": "沪深300", "type": "index"},
        {"symbol": "SZ399006", "name": "创业板指", "type": "index"},
        {"symbol": "SH000688", "name": "科创50", "type": "index"},
    ]
    for idx in INDICES:
        if q_lower in idx["symbol"].lower() or q_lower in idx["name"].lower():
            results.append(idx)

    # 1. 搜索 A 股股票（从 stock_pool.db，全 A 股约 5000+ 只）
    try:
        pool_db = settings.data_dir / "stock_pool.db"
        if pool_db.exists():
            conn = sqlite3.connect(str(pool_db))
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # LIKE 模糊匹配 symbol 或 name，排除 ST 股，按市值降序
            cursor.execute(
                "SELECT symbol, name, industry, market, market_cap FROM stock_pool "
                "WHERE is_st = 0 AND (lower(symbol) LIKE ? OR lower(name) LIKE ?) "
                "ORDER BY market_cap DESC LIMIT 30",
                (f"%{q_lower}%", f"%{q_lower}%")
            )
            for row in cursor.fetchall():
                sym = row["symbol"]
                # 构造交易所前缀格式（6开头→SH, 0/3/8开头→SZ）
                if sym.startswith("6"):
                    full_sym = f"SH{sym}"
                else:
                    full_sym = f"SZ{sym}"
                results.append({
                    "symbol": full_sym,
                    "name": row["name"],
                    "type": "stock",
                    "industry": row["industry"] or "",
                    "market_cap": row["market_cap"] or 0,
                })
            conn.close()
    except Exception as e:
        print(f"Stock pool search failed: {e}")

    # 2. 搜索 ETF 池（从 cache.db，指定 data_dir 为项目 data 目录避免相对路径问题）
    try:
        sys_path = str(settings.xueqiu_dir)
        if sys_path not in __import__('sys').path:
            __import__('sys').path.insert(0, sys_path)
        from xueqiu_engine import XueqiuEngine

        import os as _os
        engine = XueqiuEngine(
            config_file=str(settings.xueqiu_dir / "config.json"),
            data_dir=str(settings.data_dir),  # 显式指定为项目 data/ 目录
        )
        print(f"[ETF搜索] data_dir={settings.data_dir}, db_file={engine.db_file}, exists={_os.path.exists(engine.db_file)}")
        etf_list = engine.get_etf_pool_from_db(limit=500)
        print(f"[ETF搜索] 读取到 {len(etf_list)} 只 ETF，匹配关键词='{q}'")

        for etf in etf_list:
            sym = etf.get("symbol", "")
            name = etf.get("name", "")
            if q_lower in sym.lower() or q_lower in name.lower():
                results.append({
                    "symbol": sym,
                    "name": name,
                    "type": "etf",
                    "sector": etf.get("sector", ""),
                })
        print(f"[ETF搜索] 匹配到 {sum(1 for r in results if r.get('type')=='etf')} 只 ETF")
    except Exception as e:
        print(f"[ETF搜索] 失败: {e}")
        import traceback
        traceback.print_exc()

    return {
        "results": results[:20],  # 最多20条
        "query": q,
    }


# ========== 历史K线 API (数据源: Tushare) ==========

def _normalize_to_ts_code(symbol: str) -> str:
    """将前端符号（如 SH600519 / 600519）转为 Tushare 格式（600519.SH）"""
    symbol = symbol.strip().upper()
    # 已经是 tushare 格式 (如 000001.SZ)
    if "." in symbol:
        return symbol
    # 带前缀 (如 SH600519)
    if symbol.startswith("SH"):
        return f"{symbol[2:]}.SH"
    if symbol.startswith("SZ"):
        return f"{symbol[2:]}.SZ"
    if symbol.startswith("BJ"):
        return f"{symbol[2:]}.BJ"
    # 纯数字
    if symbol.startswith("6"):
        return f"{symbol}.SH"
    elif symbol.startswith(("0", "3")):
        return f"{symbol}.SZ"
    elif symbol.startswith(("8", "4")):
        return f"{symbol}.BJ"
    return symbol


@router.get("/kline/{symbol}", response_model=KlineResponse)
async def get_stock_kline(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 YYYYMMDD，默认90天前"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYYMMDD，默认今天"),
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
):
    """
    获取A股个股历史日K线数据（未复权）。
    数据源: Tushare pro daily 接口。
    
    参数示例:
    - symbol: SH600519 或 600519 或 600519.SH
    - start_date: 20240101
    - end_date: 20240524
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        ts_code = _normalize_to_ts_code(symbol)

        # 默认日期范围: 近90天
        from datetime import datetime as dt, timedelta
        if not end_date:
            end_date = dt.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (dt.now() - timedelta(days=90)).strftime("%Y%m%d")

        df = pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

        if df is None or df.empty:
            return KlineResponse(
                symbol=ts_code,
                klines=[],
                count=0,
                updated_at=datetime.now(),
            )

        # 按交易日期降序排列（最新在前），限制条数
        df = df.sort_values("trade_date", ascending=False).head(limit)

        klines = []
        for _, row in df.iterrows():
            klines.append(KlineData(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                pre_close=float(row["pre_close"]),
                change=float(row["change"]),
                pct_chg=float(row["pct_chg"]),
                vol=float(row["vol"]),
                amount=float(row["amount"]),
            ))

        return KlineResponse(
            symbol=ts_code,
            klines=klines,
            count=len(klines),
            updated_at=datetime.now(),
        )

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取K线失败: {str(e)}")


# ========== 资金流向 API (数据源: Tushare moneyflow) ==========

@router.get("/moneyflow/{symbol}", response_model=MoneyflowResponse)
async def get_stock_moneyflow(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 YYYYMMDD，默认30天前"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYYMMDD，默认今天"),
    limit: int = Query(30, ge=1, le=100, description="返回条数上限"),
):
    """
    获取A股个股资金流向数据，分析大单、小单成交情况。
    数据源: Tushare pro moneyflow 接口。
    
    成交单分类:
    - 小单: <5万 | 中单: 5-20万 | 大单: 20-100万 | 特大单: ≥100万
    
    参数示例:
    - symbol: SH600519 或 600519 或 600519.SH
    - start_date: 20240101
    - end_date: 20240524
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        ts_code = _normalize_to_ts_code(symbol)

        # 默认日期范围: 近30天
        from datetime import datetime as dt, timedelta
        if not end_date:
            end_date = dt.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (dt.now() - timedelta(days=30)).strftime("%Y%m%d")

        df = pro.moneyflow(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            limit=limit,
        )

        if df is None or df.empty:
            return MoneyflowResponse(
                symbol=ts_code,
                flows=[],
                count=0,
                updated_at=datetime.now(),
            )

        df = df.sort_values("trade_date", ascending=False).head(limit)

        flows = []
        for _, row in df.iterrows():
            flows.append(MoneyflowData(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                buy_sm_vol=int(row.get("buy_sm_vol", 0) or 0),
                buy_sm_amount=float(row.get("buy_sm_amount", 0) or 0),
                sell_sm_vol=int(row.get("sell_sm_vol", 0) or 0),
                sell_sm_amount=float(row.get("sell_sm_amount", 0) or 0),
                buy_md_vol=int(row.get("buy_md_vol", 0) or 0),
                buy_md_amount=float(row.get("buy_md_amount", 0) or 0),
                sell_md_vol=int(row.get("sell_md_vol", 0) or 0),
                sell_md_amount=float(row.get("sell_md_amount", 0) or 0),
                buy_lg_vol=int(row.get("buy_lg_vol", 0) or 0),
                buy_lg_amount=float(row.get("buy_lg_amount", 0) or 0),
                sell_lg_vol=int(row.get("sell_lg_vol", 0) or 0),
                sell_lg_amount=float(row.get("sell_lg_amount", 0) or 0),
                buy_elg_vol=int(row.get("buy_elg_vol", 0) or 0),
                buy_elg_amount=float(row.get("buy_elg_amount", 0) or 0),
                sell_elg_vol=int(row.get("sell_elg_vol", 0) or 0),
                sell_elg_amount=float(row.get("sell_elg_amount", 0) or 0),
                net_mf_vol=int(row.get("net_mf_vol", 0) or 0),
                net_mf_amount=float(row.get("net_mf_amount", 0) or 0),
            ))

        return MoneyflowResponse(
            symbol=ts_code,
            flows=flows,
            count=len(flows),
            updated_at=datetime.now(),
        )

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取资金流向失败: {str(e)}")


# ========== 技术面因子 API (数据源: Tushare stk_factor_pro) ==========

@router.get("/technical/{symbol}", response_model=TechnicalResponse)
async def get_stock_technical(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 YYYYMMDD，默认90天前"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYYMMDD，默认今天"),
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
):
    """
    获取A股个股技术面因子数据，包含 MACD、KDJ、RSI、布林带等60+技术指标。
    数据源: Tushare pro stk_factor_pro 接口。

    主要指标参数:
    - MACD: 12,26,9 | KDJ: 9,3,3 | RSI: 6,12,24 | BOLL: 20,2 | ATR: 20 | CCI: 14

    参数示例:
    - symbol: SH600519 或 600519 或 600519.SH
    - start_date: 20240101
    - end_date: 20240524
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        ts_code = _normalize_to_ts_code(symbol)

        from datetime import datetime as dt, timedelta
        if not end_date:
            end_date = dt.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (dt.now() - timedelta(days=90)).strftime("%Y%m%d")

        df = pro.stk_factor_pro(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )

        if df is None or df.empty:
            return TechnicalResponse(
                symbol=ts_code,
                data=[],
                count=0,
                updated_at=datetime.now(),
            )

        df = df.sort_values("trade_date", ascending=False).head(limit)

        records = []
        for _, row in df.iterrows():
            records.append(TechnicalData(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                close=float(row.get("close", 0) or 0),
                macd=float(row.get("macd", 0) or 0),
                macd_dif=float(row.get("macd_dif", 0) or 0),
                macd_dea=float(row.get("macd_dea", 0) or 0),
                kdj=float(row.get("kdj", 0) or 0),
                kdj_k=float(row.get("kdj_k", 0) or 0),
                kdj_d=float(row.get("kdj_d", 0) or 0),
                rsi_6=float(row.get("rsi_6", 0) or 0),
                rsi_12=float(row.get("rsi_12", 0) or 0),
                rsi_24=float(row.get("rsi_24", 0) or 0),
                boll_upper=float(row.get("boll_upper", 0) or 0),
                boll_mid=float(row.get("boll_mid", 0) or 0),
                boll_lower=float(row.get("boll_lower", 0) or 0),
                atr=float(row.get("atr", 0) or 0),
                cci=float(row.get("cci", 0) or 0),
                wr=float(row.get("wr", 0) or 0),
            ))

        return TechnicalResponse(
            symbol=ts_code,
            data=records,
            count=len(records),
            updated_at=datetime.now(),
        )

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取技术指标失败: {str(e)}")


# ========== pro_bar 通用行情 API (数据源: Tushare pro_bar) ==========

@router.get("/pro-bar/{symbol}", response_model=ProBarResponse)
async def get_stock_pro_bar(
    symbol: str,
    start_date: Optional[str] = Query(None, description="开始日期 YYYYMMDD，默认90天前"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYYMMDD，默认今天"),
    adj: Optional[str] = Query(None, description="复权类型: None(不复权) / qfq(前复权) / hfq(后复权)，仅股票有效"),
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
):
    """
    获取A股个股日K线行情（pro_bar 通用接口），支持复权和均线。
    数据源: Tushare pro_bar 接口。

    与 daily 接口的区别:
    - pro_bar 支持前复权(qfq)/后复权(hfq)，daily 仅支持不复权
    - pro_bar 可指定 ma 均线参数（MA5/MA10/MA20等）
    - pro_bar 不返回 pre_close/change/pct_chg（需自行计算）
    - pro_bar 积分要求 600+，daily 仅需 120 积分

    参数示例:
    - symbol: SH600519
    - adj: qfq 或 hfq 或 不填（默认不复权）
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        ts_code = _normalize_to_ts_code(symbol)

        from datetime import datetime as dt, timedelta
        if not end_date:
            end_date = dt.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (dt.now() - timedelta(days=90)).strftime("%Y%m%d")

        df = pro.bar(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            adj=adj,
            freq='D',
        )

        if df is None or df.empty:
            return ProBarResponse(
                symbol=ts_code,
                bars=[],
                count=0,
                updated_at=datetime.now(),
            )

        df = df.sort_values("trade_date", ascending=False).head(limit)

        bars = []
        for _, row in df.iterrows():
            bars.append(ProBarData(
                ts_code=str(row["ts_code"]),
                trade_date=str(row["trade_date"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                vol=float(row["vol"]),
                amount=float(row["amount"]),
                adj=adj,
            ))

        return ProBarResponse(
            symbol=ts_code,
            bars=bars,
            count=len(bars),
            updated_at=datetime.now(),
        )

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取pro_bar行情失败: {str(e)}")


# ========== 概念板块 API (数据源: stock_pool.db) ==========

@router.get("/concept")
async def get_concept_mapping(
    concept: Optional[str] = Query(None, description="概念名称，如 人形机器人。不传则返回所有概念列表"),
    limit: int = Query(30, ge=1, le=200, description="返回数量上限"),
):
    """
    查询概念板块及成分股映射。
    
    - 不传 concept: 返回所有概念板块列表（名称+成分股数量）
    - 传 concept: 返回该概念下的所有成分股详情（ts_code/symbol/name/market_cap）
    
    数据源: stock_pool.db (sectors + stock_concept_map + stock_pool)
    """
    import sqlite3
    from pathlib import Path
    
    try:
        # 找到 stock_pool.db
        pool_db = Path(__file__).parent.parent.parent / "data" / "stock_pool.db"
        if not pool_db.exists():
            return {"error": "stock_pool.db 不存在", "concepts": [], "stocks": [], "total": 0}
        
        conn = sqlite3.connect(str(pool_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if not concept:
            # 列出所有概念板块
            cursor.execute(
                "SELECT sector_name, stock_count FROM sectors "
                "WHERE sector_type = 'concept' "
                "ORDER BY stock_count DESC LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
            total_cursor = conn.execute(
                "SELECT COUNT(*) FROM sectors WHERE sector_type = 'concept'"
            )
            total = total_cursor.fetchone()[0]
            conn.close()
            
            return {
                "concepts": [{"sector_name": r["sector_name"], "stock_count": r["stock_count"]} for r in rows],
                "total": total,
            }
        else:
            # 查询某一概念下的股票
            cursor.execute(
                "SELECT p.ts_code, p.symbol, p.name, p.market_cap "
                "FROM stock_concept_map m "
                "JOIN stock_pool p ON p.ts_code = m.ts_code "
                "WHERE m.concept_name = ? "
                "ORDER BY p.market_cap DESC LIMIT ?",
                (concept.strip(), limit)
            )
            rows = cursor.fetchall()
            
            # 同时获取概念信息
            cursor.execute(
                "SELECT sector_name, stock_count FROM sectors WHERE sector_name = ?",
                (concept.strip(),)
            )
            concept_row = cursor.fetchone()
            
            conn.close()
            
            return {
                "concept": concept,
                "stock_count": concept_row["stock_count"] if concept_row else 0,
                "stocks": [
                    {
                        "ts_code": r["ts_code"],
                        "symbol": r["symbol"],
                        "name": r["name"],
                        "market_cap": r["market_cap"] or 0,
                    }
                    for r in rows
                ],
                "concepts": [
                    {"sector_name": concept_row["sector_name"], "stock_count": concept_row["stock_count"]}
                ] if concept_row else [],
                "total": 1 if concept_row else 0,
            }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询概念板块失败: {str(e)}")


# ========== 概念板块行情 API (数据源: 东财 push2 实时 + Tushare dc_daily 兜底) ==========

@router.get("/concept-fund-flow")
async def get_concept_fund_flow(
    limit: int = Query(15, ge=1, le=100, description="返回数量上限"),
    sort_by: str = Query("pct_change", description="排序字段: pct_change(涨幅) / main_net(主力净流入)"),
):
    """
    获取概念板块实时行情排行，支持按涨幅或主力资金流向排序。

    数据源（优先级）:
    1. 东财 push2 实时接口（盘中实时更新，含涨跌幅+资金拆分明细+板块广度+领涨股）
    2. Tushare dc_daily（降级兜底，日频数据，含历史成交量/换手率）

    返回字段:
    - name/code: 概念名称/代码
    - pct_change: 涨跌幅(%)
    - main_net/main_net_fmt/main_net_rate: 主力净流入(万元/格式化/占比%)
    - super_large_net/large_net/medium_net/small_net: 四类单净流入(万元, NET)
    - advancing/declining/total_stocks: 上涨/下跌/总家数
    - lead_stock_name/lead_stock_code: 领涨股
    - flow_nature: 资金性质(主力建仓/温和流入/平衡/温和流出/主力出货)
    - vol/amount/turnover_rate: 量价数据(仅Tushare降级时可用)
    """
    from datetime import datetime as dt

    # ── 优先：东财 push2 实时接口 ──
    if _EM_FLOW_AVAILABLE:
        try:
            if sort_by == "main_net":
                flow_data = get_top_inflow_sectors("concept", top_n=limit * 2, use_cache=True)
                # 按主力净流入排序取 top_n
                flow_data.sort(key=lambda x: x["main_net"], reverse=True)
            else:
                flow_data = get_top_change_sectors("concept", top_n=limit * 2, use_cache=True)
                # 按涨跌幅排序取 top_n
                flow_data.sort(key=lambda x: x["pct_change"], reverse=True)

            sectors = []
            for fd in flow_data[:limit]:
                item = {
                    "name": fd["name"],
                    "code": fd["code"],
                    "pct_change": fd["pct_change"],
                    "main_net": fd["main_net"],
                    "main_net_fmt": fd["main_net_fmt"],
                    "main_net_rate": fd["main_net_rate"],
                    "super_large_net": fd["super_large_net"],
                    "super_large_net_rate": fd["super_large_net_rate"],
                    "large_net": fd["large_net"],
                    "large_net_rate": fd["large_net_rate"],
                    "medium_net": fd["medium_net"],
                    "medium_net_rate": fd["medium_net_rate"],
                    "small_net": fd["small_net"],
                    "small_net_rate": fd["small_net_rate"],
                    "advancing": fd["advancing"],
                    "declining": fd["declining"],
                    "total_stocks": fd["total_stocks"],
                    "lead_stock_name": fd.get("lead_stock_name", ""),
                    "lead_stock_code": fd.get("lead_stock_code", ""),
                    "flow_nature": classify_flow_nature(fd["main_net"], fd["main_net_rate"]),
                    # Tushare 字段填充空值
                    "vol": 0, "amount": 0, "turnover_rate": 0, "ts_code": "",
                }
                sectors.append(item)

            logger.info(f"[concept-fund-flow] 东财实时: {len(sectors)} 个概念 (sort={sort_by})")
            return {
                "sectors": sectors,
                "count": len(sectors),
                "sort_by": sort_by,
                "data_source": "东财push2(realtime)",
                "trade_date": dt.now().strftime("%Y%m%d"),
            }
        except Exception as e:
            logger.warning(f"[concept-fund-flow] 东财实时获取失败，降级到Tushare: {e}")

    # ── 降级：Tushare dc_daily 日频数据 ──
    try:
        import pandas as pd
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        pro = ts.pro_api(token)

        from datetime import timedelta
        now = dt.now()
        is_intraday = now.hour < 15 or (now.hour == 15 and now.minute < 15)
        start_offset = 1 if is_intraday else 0

        sectors = []
        trade_date = ""

        for offset in range(start_offset, start_offset + 3):
            attempt_date = (now - timedelta(days=offset)).strftime("%Y%m%d")
            attempt_dt = now - timedelta(days=offset)
            if attempt_dt.weekday() >= 5:
                continue
            try:
                df_daily = pro.dc_daily(
                    trade_date=attempt_date, idx_type='概念板块',
                    fields='ts_code,pct_change,vol,amount,turnover_rate'
                )
                df_index = pro.dc_index(
                    trade_date=attempt_date, idx_type='概念板块',
                    fields='ts_code,name'
                )
                if df_daily is not None and len(df_daily) > 0 and df_index is not None and len(df_index) > 0:
                    df = pd.merge(df_index, df_daily, on='ts_code', how='inner')
                    df = df.sort_values('pct_change', ascending=False)
                    trade_date = attempt_date
                    for _, row in df.head(limit).iterrows():
                        sectors.append({
                            "name": row["name"],
                            "ts_code": row["ts_code"],
                            "code": row["ts_code"],
                            "pct_change": round(float(row["pct_change"] or 0), 2),
                            "vol": float(row["vol"] or 0),
                            "amount": float(row["amount"] or 0),
                            "turnover_rate": round(float(row["turnover_rate"] or 0), 2),
                        })
                    break
            except Exception:
                continue

        return {
            "sectors": sectors,
            "count": len(sectors),
            "sort_by": sort_by,
            "trade_date": trade_date,
            "data_source": "Tushare(daily)",
        }

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取概念行情失败: {str(e)}")


# ========== 大盘资金流向 API (与 jobs/fund_flow._fetch_market_moneyflow_dc 同源) ==========

@router.get("/moneyflow-mkt")
async def get_moneyflow_mkt(
    trade_date: str = Query(None, description="交易日期，YYYYMMDD。不传则使用实时数据"),
    source: str = Query("auto", description="数据源: auto(自动,优先实时) / realtime(东财实时,仅当日) / tushare(Tushare日频)"),
):
    """
    获取大盘资金流向数据（主力/超大单/大单/中单/小单净流入）。

    数据源:
    - 默认(auto): 盘中优先用东财push2实时接口(沪深分开+合计)，盘后用Tushare日频
    - realtime: 东财 push2 ulist.np 实时接口，含买/卖分明细
    - tushare: Tushare moneyflow_mkt_dc，支持历史日期查询

    返回字段(兼容新旧格式):
    - data.trade_date: 交易日期
    - data.close_sh/pct_change_sh: 上证收盘/涨跌幅 (仅Tushare)
    - data.net_amount/net_amount_fmt: 主力净流入(万元/格式化)
    - data.flow_nature: 资金性质
    - data.sh/sz: 沪深分开明细 (仅realtime)
    - data.combined: 两市合计 (仅realtime)
    - data.buy_elg_amount/buy_lg_amount/...: 各单净流入(万元)
    - data.data_source: 数据来源标识
    """
    from datetime import datetime as dt

    # ── 实时数据（东财 push2 ulist.np）──
    if source in ("auto", "realtime") and not trade_date and _EM_FLOW_AVAILABLE:
        try:
            from utils.em_sector_flow import get_market_moneyflow_realtime
            rt = get_market_moneyflow_realtime()
            if rt:
                combined = rt["combined"]
                # 构建向后兼容的扁平结构
                data = {
                    "trade_date": dt.now().strftime("%Y%m%d"),
                    "close_sh": 0,  # 实时接口无收盘价
                    "pct_change_sh": 0,
                    "close_sz": 0,
                    "pct_change_sz": 0,
                    "net_amount": combined["main_net"],
                    "net_amount_rate": combined.get("main_net_rate", 0),
                    "net_amount_fmt": combined["main_net_fmt"],
                    "net_amount_yi": round(combined["main_net"] / 10000, 2),
                    "flow_nature": rt["flow_nature"],
                    "total_amount": combined["total_amount"],
                    "total_amount_fmt": combined["total_amount_fmt"],
                    # 各单净流入
                    "buy_elg_amount": combined["super_large_net"],
                    "buy_elg_amount_rate": combined.get("super_large_net_rate", 0),
                    "buy_lg_amount": combined["large_net"],
                    "buy_lg_amount_rate": combined.get("large_net_rate", 0),
                    "buy_md_amount": combined["medium_net"],
                    "buy_md_amount_rate": combined.get("medium_net_rate", 0),
                    "buy_sm_amount": combined["small_net"],
                    "buy_sm_amount_rate": combined.get("small_net_rate", 0),
                    # 买/卖分明细
                    "super_large_buy": combined.get("super_large_buy", 0),
                    "super_large_sell": combined.get("super_large_sell", 0),
                    "large_buy": combined.get("large_buy", 0),
                    "large_sell": combined.get("large_sell", 0),
                    "medium_buy": combined.get("medium_buy", 0),
                    "medium_sell": combined.get("medium_sell", 0),
                    "small_buy": combined.get("small_buy", 0),
                    "small_sell": combined.get("small_sell", 0),
                    "source": rt["source"],
                    "data_source": "实时(东财push2)",
                }
                # 附加沪深分开
                result = {"data": data, "success": True,
                          "sh": rt["sh"], "sz": rt["sz"],
                          "combined": combined,
                          "updated_at": rt["updated_at"]}
                logger.info(f"[moneyflow-mkt] 东财实时: {data['net_amount_fmt']} | {data['flow_nature']}")
                return result
        except Exception as e:
            logger.warning(f"[moneyflow-mkt] 东财实时获取失败，降级到Tushare: {e}")

    # ── 降级：Tushare 日频数据 ──
    from pathlib import Path
    import sys
    from app.config import get_settings
    settings = get_settings()

    _jobs_dir = str(settings.workspace_path / "jobs")
    _core_dir = str(settings.workspace_path / "core")
    if _jobs_dir not in sys.path:
        sys.path.insert(0, _jobs_dir)
    if _core_dir not in sys.path:
        sys.path.insert(0, _core_dir)

    try:
        from fund_flow import _fetch_market_moneyflow_dc

        if trade_date:
            mkt_data = _fetch_market_moneyflow_dc(trade_date)
        else:
            mkt_data = None
            from datetime import timedelta
            now = dt.now()
            is_intraday = now.hour < 15 or (now.hour == 15 and now.minute < 15)
            start = 1 if is_intraday else 0
            for offset in range(start, start + 3):
                attempt_dt = now - timedelta(days=offset)
                if attempt_dt.weekday() >= 5:
                    continue
                attempt_date = attempt_dt.strftime("%Y%m%d")
                mkt_data = _fetch_market_moneyflow_dc(attempt_date)
                if mkt_data:
                    break

        if not mkt_data:
            return {"data": None, "trade_date": trade_date or "", "message": "无数据"}

        data = {
            "trade_date": mkt_data["trade_date"],
            "close_sh": mkt_data.get("close_sh", 0),
            "pct_change_sh": mkt_data.get("pct_change_sh", 0),
            "close_sz": mkt_data.get("close_sz", 0),
            "pct_change_sz": mkt_data.get("pct_change_sz", 0),
            "net_amount": mkt_data["net_amount"],
            "net_amount_rate": mkt_data.get("net_amount_rate", 0),
            "net_amount_fmt": mkt_data["net_amount_fmt"],
            "net_amount_yi": mkt_data.get("net_amount_yi", 0),
            "flow_nature": mkt_data["flow_nature"],
            "buy_elg_amount": mkt_data.get("buy_elg_amount", 0),
            "buy_elg_amount_rate": mkt_data.get("buy_elg_amount_rate", 0),
            "buy_lg_amount": mkt_data.get("buy_lg_amount", 0),
            "buy_lg_amount_rate": mkt_data.get("buy_lg_amount_rate", 0),
            "buy_md_amount": mkt_data.get("buy_md_amount", 0),
            "buy_md_amount_rate": mkt_data.get("buy_md_amount_rate", 0),
            "buy_sm_amount": mkt_data.get("buy_sm_amount", 0),
            "buy_sm_amount_rate": mkt_data.get("buy_sm_amount_rate", 0),
            "source": mkt_data.get("source", "tushare_daily"),
            "data_source": "日频(Tushare)",
        }

        return {"data": data, "success": True}

    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取大盘资金流向失败: {str(e)}")


# ========== 市场宽度 API (数据源: Tushare limit_list_d + daily_basic) ==========

@router.get("/breadth")
async def get_market_breadth():
    """
    获取市场宽度数据（涨跌家数 / 涨跌停家数 / 成交额）。
    数据源: Tushare limit_list_d + daily_basic。
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        import pandas as pd
        from datetime import datetime as dt, timedelta

        pro = ts.pro_api(token)

        # 默认值
        advancing = 0
        declining = 0
        unchanged = 0
        limit_up = 0
        limit_down = 0
        total_amount = 0
        trade_date = ""

        # 1. 先通过 daily 接口确定交易日并获取涨跌家数、成交额
        for offset in range(3):
            attempt_date = (dt.now() - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                logger.info(f"[breadth] 尝试获取 daily 数据，trade_date={attempt_date}")
                daily_df = pro.daily(trade_date=attempt_date)
                if daily_df is not None and len(daily_df) > 0:
                    trade_date = attempt_date
                    logger.info(f"[breadth] daily 数据获取成功，行数={len(daily_df)}, 列={list(daily_df.columns)}")
                    if 'pct_chg' in daily_df.columns:
                        advancing = int((daily_df['pct_chg'] > 0).sum())
                        declining = int((daily_df['pct_chg'] < 0).sum())
                        unchanged = int((daily_df['pct_chg'] == 0).sum())
                    if 'amount' in daily_df.columns:
                        total_amount = float(daily_df['amount'].sum()) / 100000  # 万→亿
                    break
                else:
                    logger.warning(f"[breadth] daily 返回空数据，trade_date={attempt_date}")
            except Exception as e:
                logger.warning(f"[breadth] daily 调用异常，trade_date={attempt_date}: {e}")
                continue

        # 2. 独立获取涨跌停数据
        if trade_date:
            try:
                logger.info(f"[breadth] 尝试获取 limit_list_d，trade_date={trade_date}")
                limit_df = pro.limit_list_d(trade_date=trade_date, limit_type='U,D')
                if limit_df is not None and len(limit_df) > 0:
                    limit_up = len(limit_df[limit_df['limit'] == 'U'])
                    limit_down = len(limit_df[limit_df['limit'] == 'D'])
                    logger.info(f"[breadth] limit_list_d 成功，涨停={limit_up}, 跌停={limit_down}")
                else:
                    logger.warning(f"[breadth] limit_list_d 返回空数据")
            except Exception as e:
                logger.warning(f"[breadth] limit_list_d 调用异常: {e}")

        return {
            "advancing": advancing,
            "declining": declining,
            "unchanged": unchanged,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "total_amount": round(total_amount, 0),
            "trade_date": trade_date or "",
        }

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取市场宽度失败: {str(e)}")


# ========== 涨跌榜 API (数据源: Tushare daily_basic) ==========

@router.get("/top-movers")
async def get_top_movers(
    type: str = Query("gainers", description="类型: gainers(涨幅榜) / losers(跌幅榜) / active(活跃榜)"),
    limit: int = Query(10, ge=1, le=50, description="返回数量"),
):
    """
    获取涨跌榜数据。
    数据源: Tushare daily_basic，从 stock_pool.db 补充股票名称。
    """
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        import tushare as ts
        import sqlite3
        from datetime import datetime as dt, timedelta

        pro = ts.pro_api(token)

        # 尝试最近3个交易日
        # 使用 daily 而非 daily_basic，因为只有 daily 接口包含 pct_chg（涨跌幅）字段
        trade_date = None
        df = None
        for offset in range(3):
            attempt_date = (dt.now() - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                df = pro.daily(trade_date=attempt_date)
                if df is not None and len(df) > 0:
                    trade_date = attempt_date
                    break
            except Exception:
                continue

        if df is None or len(df) == 0:
            return {"movers": [], "type": type, "trade_date": ""}

        # 防御性检查：确认必要列存在
        if type in ("gainers", "losers") and "pct_chg" not in df.columns:
            return {"movers": [], "type": type, "trade_date": trade_date or ""}
        if type == "active" and "amount" not in df.columns:
            return {"movers": [], "type": type, "trade_date": trade_date or ""}

        if type == "gainers":
            df = df.sort_values("pct_chg", ascending=False).head(limit)
        elif type == "losers":
            df = df.sort_values("pct_chg", ascending=True).head(limit)
        else:  # active
            df = df.sort_values("amount", ascending=False).head(limit)

        # 从 stock_pool.db 获取股票名称
        name_map = {}
        try:
            pool_db = settings.data_dir / "stock_pool.db"
            if pool_db.exists():
                conn = sqlite3.connect(str(pool_db))
                cursor = conn.cursor()
                ts_codes = [str(c) for c in df["ts_code"].tolist()]
                placeholders = ",".join(["?"] * len(ts_codes))
                cursor.execute(
                    f"SELECT ts_code, name FROM stock_pool WHERE ts_code IN ({placeholders})",
                    ts_codes
                )
                for row in cursor.fetchall():
                    name_map[row[0]] = row[1]
                conn.close()
        except Exception:
            pass

        movers = []
        for _, row in df.iterrows():
            ts_code = str(row["ts_code"])
            movers.append({
                "symbol": ts_code,
                "name": name_map.get(ts_code, ts_code),
                "current_price": round(float(row.get("close", 0) or 0), 2),
                "change_pct": round(float(row.get("pct_chg", 0) or 0), 2),
            })

        return {
            "movers": movers,
            "type": type,
            "trade_date": trade_date or "",
        }

    except EnvironmentError as e:
        raise HTTPException(status_code=503, detail=f"Tushare 配置错误: {str(e)}")
    except ImportError:
        raise HTTPException(status_code=503, detail="tushare 库未安装，请 pip install tushare")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取涨跌榜失败: {str(e)}")


# ========== 交易日状态 API (数据源: Tushare trade_cal) ==========

@router.get("/trade-status")
async def get_trade_status():
    """
    获取当前交易日状态和交易时段。
    数据源: Tushare trade_cal API。

    返回:
    - is_trade_day: 今天是否为交易日 (bool)
    - trading_status: 当前交易状态
        'pre_open': 盘前（9:15前）
        'call_auction': 集合竞价（9:15-9:25）
        'waiting_open': 等待开盘（9:25-9:30）
        'morning_session': 早盘交易中（9:30-11:30）
        'lunch_break': 午间休市（11:30-13:00）
        'afternoon_session': 午盘交易中（13:00-15:00）
        'closed': 已收盘
        'holiday': 节假日休市
        'weekend': 周末休市
    - status_label: 状态中文描述
    - current_time: 当前时间 (ISO格式)
    - trade_date: 最近交易日 (YYYY-MM-DD)
    """
    from datetime import datetime as dt

    now = dt.now()
    current_time = now.isoformat()
    weekday = now.weekday()  # 0=周一, 6=周日
    hour, minute = now.hour, now.minute
    time_minutes = hour * 60 + minute

    # 先判断是否交易日（使用 Tushare trade_cal）
    is_trade_day = True
    trade_reason = ""

    if weekday >= 5:  # 周末
        is_trade_day = False
        trade_reason = "周末休市"
    else:
        try:
            sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "core"))
            from utils.trade_day_utils import is_today_trade_day
            is_trade_day, trade_reason = is_today_trade_day()
        except ImportError:
            # fallback: 假设是交易日
            trade_reason = "（Tushare不可用，默认视为交易日）"
        except Exception:
            trade_reason = "（交易日检测异常，默认视为交易日）"

    # 确定交易状态
    if not is_trade_day:
        if weekday >= 5:
            trading_status = "weekend"
            status_label = "🔴 周末休市"
        else:
            trading_status = "holiday"
            status_label = "🔴 节假日休市"
    elif time_minutes < 9 * 60 + 15:
        trading_status = "pre_open"
        status_label = "⏳ 尚未开盘，等待集合竞价（9:15 开始）"
    elif time_minutes < 9 * 60 + 25:
        trading_status = "call_auction"
        status_label = "🟡 集合竞价中（9:15-9:25）"
    elif time_minutes < 9 * 60 + 30:
        trading_status = "waiting_open"
        status_label = "🟡 集合竞价结束，等待连续竞价（9:30 开盘）"
    elif time_minutes < 11 * 60 + 30:
        trading_status = "morning_session"
        status_label = "🟢 早盘交易中（9:30-11:30）"
    elif time_minutes < 13 * 60:
        trading_status = "lunch_break"
        status_label = "🔴 午间休市（11:30-13:00）"
    elif time_minutes < 15 * 60:
        trading_status = "afternoon_session"
        status_label = "🟢 午盘交易中（13:00-15:00）"
    else:
        trading_status = "closed"
        status_label = "🔴 今日已收盘"

    # 获取最近交易日
    trade_date = None
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "core"))
        from utils.trade_day_utils import get_latest_trade_day
        trade_date, _ = get_latest_trade_day(method='auto')
    except Exception:
        trade_date = now.strftime("%Y-%m-%d")

    return {
        "is_trade_day": is_trade_day,
        "trading_status": trading_status,
        "status_label": status_label,
        "current_time": current_time,
        "time_display": now.strftime("%Y年%m月%d日 %H:%M:%S") + " " + ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][weekday],
        "trade_date": trade_date,
        "reason": trade_reason,
    }


# ========== 东财实时板块资金流向 API (数据源: 东财 push2) ==========

@router.get("/sector-flow")
async def get_sector_flow_endpoint(
    type: str = Query("concept", description="板块类型: concept(概念) / industry(行业) / region(地域)"),
    sort_by: str = Query("main_net", description="排序字段: main_net(主力净流入) / pct_change(涨跌幅) / main_net_rate(主力占比)"),
    limit: int = Query(20, ge=1, le=200, description="返回数量上限"),
):
    """
    获取东财实时板块资金流向排名。

    数据源: 东方财富 push2 实时接口，盘中实时更新。
    相比 /concept-fund-flow 的 Tushare 日频数据，此接口提供:
    - 实时主力/超大单/大单/中单/小单拆分明细
    - 板块上涨/下跌家数（广度指标）
    - 主力净流入占比

    返回字段:
    - code: 板块代码
    - name: 板块名称
    - pct_change: 涨跌幅(%)
    - main_net: 主力净流入(万元)
    - main_net_fmt: 主力净流入(格式化，如 +12.50亿)
    - main_net_rate: 主力净流入占比(%)
    - super_large_net: 超大单净流入(万元)
    - large_net: 大单净流入(万元)
    - medium_net: 中单净流入(万元)
    - small_net: 小单净流入(万元)
    - advancing: 上涨家数
    - declining: 下跌家数
    - total_stocks: 成分股总数
    - flow_nature: 资金性质(主力建仓/温和流入/平衡/温和流出/主力出货)

    示例:
    - 概念板块主力净流入排名: GET /market/sector-flow?type=concept&sort_by=main_net
    - 行业板块涨幅排名: GET /market/sector-flow?type=industry&sort_by=pct_change
    """
    if not _EM_FLOW_AVAILABLE:
        raise HTTPException(status_code=503, detail="东财实时板块资金流模块不可用")

    try:
        sectors = get_sector_flow(
            sector_type=type,
            sort_by=sort_by,
            top_n=limit,
            use_cache=True,
        )

        # 附加资金性质分类
        for s in sectors:
            s["flow_nature"] = classify_flow_nature(
                s["main_net"], s["main_net_rate"]
            )

        return {
            "sectors": sectors,
            "count": len(sectors),
            "type": type,
            "sort_by": sort_by,
            "data_source": "东财push2(realtime)",
            "updated_at": datetime.now().isoformat(),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取板块资金流失败: {str(e)}")


@router.get("/sector-summary")
async def get_sector_summary_endpoint(
    top_n: int = Query(5, ge=1, le=20, description="每个维度返回数量"),
):
    """
    获取市场板块资金流综合摘要（概念 + 行业双维度）。

    同时返回:
    - 主力净流入 Top 概念 / 行业
    - 涨幅 Top 概念 / 行业

    用于盘中快速判断当日最强方向。
    """
    if not _EM_FLOW_AVAILABLE:
        raise HTTPException(status_code=503, detail="东财实时板块资金流模块不可用")

    try:
        summary = get_market_sector_summary(top_n=top_n)

        # 为每个板块附加资金性质
        for key in ["top_inflow_concepts", "top_inflow_industries",
                     "top_change_concepts", "top_change_industries"]:
            for s in summary.get(key, []):
                s["flow_nature"] = classify_flow_nature(
                    s["main_net"], s["main_net_rate"]
                )

        return {
            **summary,
            "data_source": "东财push2(realtime)",
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取板块摘要失败: {str(e)}")


@router.get("/sector-flow/{name}")
async def get_sector_flow_by_name_endpoint(
    name: str,
    type: str = Query("concept", description="板块类型: concept / industry"),
):
    """
    按名称模糊查询单个板块的实时资金流向。

    示例:
    - GET /market/sector-flow/半导体?type=concept
    - GET /market/sector-flow/银行?type=industry
    """
    if not _EM_FLOW_AVAILABLE:
        raise HTTPException(status_code=503, detail="东财实时板块资金流模块不可用")

    try:
        result = get_sector_flow_by_name(name, sector_type=type, use_cache=True)
        if not result:
            raise HTTPException(status_code=404, detail=f"未找到板块: {name}")

        result["flow_nature"] = classify_flow_nature(
            result["main_net"], result["main_net_rate"]
        )
        result["data_source"] = "东财push2(realtime)"

        return result

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询板块资金流失败: {str(e)}")
