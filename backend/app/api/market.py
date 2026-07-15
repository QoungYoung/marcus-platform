# -*- coding: utf-8 -*-
"""
Market data API endpoints.
"""
import logging
import sys
import os
import time
from datetime import datetime
from typing import Optional
from pathlib import Path

logger = logging.getLogger(__name__)

from fastapi import APIRouter, HTTPException, Query

from app.models.market import IndexResponse, IndicesResponse, QuoteResponse, SectorResponse, SectorsResponse, GlobalMarketResponse, GlobalIndexResponse, CommodityResponse, KlineData, KlineResponse, MoneyflowData, MoneyflowResponse, TechnicalData, TechnicalResponse, ProBarData, ProBarResponse, ThsMoneyflowResponse

router = APIRouter(prefix="/market", tags=["Market Data"])

# 项目根目录（用于读 SQLite 缓存）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Import XUEQIU_DIR from workspace_detector (core package)
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
    # 检查环境变量 SKIP_EASTMONEY（云服务器被东财封 IP 时快速跳过）
    import os as _os
    if _os.getenv("SKIP_EASTMONEY", "").strip().lower() in ("1", "true", "yes", "on"):
        _EM_FLOW_AVAILABLE = False
        logger.info("[market] SKIP_EASTMONEY=true，东财实时接口已禁用，将走 Tushare 降级")
    else:
        logger.info("[market] ✅ 东财实时板块资金流模块已加载")
except ImportError:
    _EM_FLOW_AVAILABLE = False
    logger.warning("[market] ⚠️ 东财实时板块资金流模块不可用")


def _fmt_net_amount(wan: float) -> str:
    """格式化净流入金额（万元）为人类可读字符串，与 _format_amount_wan 保持一致。

    Args:
        wan: 金额（万元）
    Returns:
        "+1.26亿" / "-345万" 等
    """
    if abs(wan) >= 10000:
        return f"{wan / 10000:+.2f}亿"
    return f"{wan:+.0f}万"


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

        # ── 计算日内价格分位 ──
        current_p = quote.get("current", 0)
        high_p = quote.get("high") or current_p
        low_p = quote.get("low") or current_p
        intraday_percentile = None
        if high_p > low_p and current_p > 0:
            intraday_percentile = round((current_p - low_p) / (high_p - low_p) * 100, 1)

        # ── 计算 RSR（相对强弱比）──
        rsr = None
        bare_code = symbol[2:] if symbol.startswith(("SH", "SZ", "BJ")) else symbol
        try:
            import sqlite3
            pool_db = settings.data_dir / "stock_pool.db"
            if pool_db.exists():
                conn = sqlite3.connect(str(pool_db))
                curs = conn.cursor()
                # 查找该股票所属的首要概念板块
                curs.execute(
                    "SELECT concept_name FROM stock_concept_map WHERE ts_code LIKE ? LIMIT 1",
                    (f"%{bare_code}%",)
                )
                row = curs.fetchone()
                if row:
                    concept_name = row[0]
                    # 从概念资金流数据中获取板块涨幅
                    try:
                        from utils.em_sector_flow import get_sector_flow_by_name
                        sector = get_sector_flow_by_name(concept_name, sector_type="concept", use_cache=True)
                        if sector and sector.get("pct_change", 0) != 0:
                            sector_pct = sector["pct_change"]
                            stock_pct = quote.get("percent", 0)
                            if sector_pct != 0:
                                rsr = round(stock_pct / sector_pct, 2)
                    except Exception:
                        pass
                conn.close()
        except Exception:
            pass

        return QuoteResponse(
            symbol=symbol,
            name=quote.get("name", ""),
            current=current_p,
            change=quote.get("chg", 0),
            percent=quote.get("percent", 0),
            last_close=quote.get("last_close", 0),
            open=quote.get("open"),
            high=high_p if high_p != current_p else None,
            low=low_p if low_p != current_p else None,
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
            rsr=rsr,
            intraday_percentile=intraday_percentile,
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

        pro = _get_tushare_pro()

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


# ========== 个股资金流向 API (数据源: 东财实时 + Tushare 日频降级) ==========

EM_PROXY_URL = os.environ.get("EM_PROXY_URL", "")  # FRP 隧道代理: http://81.70.44.68:8199

def _safe_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _get_tushare_pro():
    """统一获取 Tushare pro_api 实例（走主代理 ts.gyzcloud.top）"""
    from app.core.trading._api_config import get_tushare_pro as _gtp
    return _gtp()



def _query_stock_flow(ts_code: str) -> Optional[dict]:
    """
    查询单只股票实时资金流（api/qt/stock/get）。
    secid: 1.600519 (沪) / 0.000878 (深)
    """
    code = ts_code.split(".")[0] if "." in ts_code else ts_code.lstrip("SHEZBJ")
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    # stock/get 字段: f137=主力 f140=超大单 f143=大单 f146=中单 f149=小单 (净额)
    #                 f193=主力% f194=超大单% f195=大单% f196=中单% f197=小单% (占比)
    fields = ("f12,f14,f2,f3,f170,"
              "f137,f140,f143,f146,f149,f193,f194,f195,f196,f197,"   # 今日
              "f434,f435,f436,f437,f438,f454,f455,f456,f457,f458,"   # 5日
              "f459,f461,f463,f465,f467,f460,f462,f464,f466,f468")  # 10日

    if EM_PROXY_URL:
        import requests as req
        resp = req.get(f"{EM_PROXY_URL}/api/qt/stock/get?secid={secid}&fields={fields}", timeout=10)
        data = resp.json()
    else:
        from curl_cffi import requests as cffi_req
        resp = cffi_req.get("https://push2.eastmoney.com/api/qt/stock/get",
            params={"secid": secid, "fields": fields},
            headers={"User-Agent": "Mozilla/5.0", "Cookie": os.environ.get("EASTMONEY_COOKIE", "")},
            impersonate="chrome124", timeout=10)
        data = resp.json()

    d = data.get("data")
    if not d:
        return None
    # 占比是万分比（如 1788 = 17.88%），除以100
    return {
        "symbol": code,
        "name": str(d.get("f14", "")),
        "price": _safe_float(d.get("f2")),
        "change_pct": str(round(_safe_float(d.get("f3")) / 100, 2)) if d.get("f3") else "0",
        "turnover_rate": str(round(_safe_float(d.get("f170")) / 100, 2)) if d.get("f170") else "0",
        "inflow": 0, "outflow": 0,
        "net_amount": _safe_float(d.get("f137")),            # 主力净额
        "main_net": _safe_float(d.get("f137")),
        "main_pct": str(round(_safe_float(d.get("f193")) / 100, 2)),
        "lg_net": _safe_float(d.get("f140")), "lg_pct": str(round(_safe_float(d.get("f194")) / 100, 2)),
        "md_net": _safe_float(d.get("f143")), "md_pct": str(round(_safe_float(d.get("f195")) / 100, 2)),
        "sm_net": _safe_float(d.get("f146")), "sm_pct": str(round(_safe_float(d.get("f196")) / 100, 2)),
        "xs_net": _safe_float(d.get("f149")), "xs_pct": str(round(_safe_float(d.get("f197")) / 100, 2)),
        # 5日
        "d5_main_net": _safe_float(d.get("f434")), "d5_main_pct": str(round(_safe_float(d.get("f454")) / 100, 2)),
        "d5_lg_net": _safe_float(d.get("f435")), "d5_lg_pct": str(round(_safe_float(d.get("f455")) / 100, 2)),
        "d5_md_net": _safe_float(d.get("f436")), "d5_md_pct": str(round(_safe_float(d.get("f456")) / 100, 2)),
        "d5_sm_net": _safe_float(d.get("f437")), "d5_sm_pct": str(round(_safe_float(d.get("f457")) / 100, 2)),
        "d5_xs_net": _safe_float(d.get("f438")), "d5_xs_pct": str(round(_safe_float(d.get("f458")) / 100, 2)),
        # 10日
        "d10_main_net": _safe_float(d.get("f459")), "d10_main_pct": str(round(_safe_float(d.get("f460")) / 100, 2)),
        "d10_lg_net": _safe_float(d.get("f461")), "d10_lg_pct": str(round(_safe_float(d.get("f462")) / 100, 2)),
        "d10_md_net": _safe_float(d.get("f463")), "d10_md_pct": str(round(_safe_float(d.get("f464")) / 100, 2)),
        "d10_sm_net": _safe_float(d.get("f465")), "d10_sm_pct": str(round(_safe_float(d.get("f466")) / 100, 2)),
        "d10_xs_net": _safe_float(d.get("f467")), "d10_xs_pct": str(round(_safe_float(d.get("f468")) / 100, 2)),
    }



@router.get("/moneyflow/{symbol}", response_model=ThsMoneyflowResponse)
async def get_stock_moneyflow(
    symbol: str,
):
    """
    获取个股资金流向数据（东方财富实时 + Tushare日频降级）。

    数据源: push2.eastmoney.com（实时，仅交易时段）+ Tushare moneyflow（日频盘后）
    返回字段:
    - inflow/outflow: 流入/流出资金（元）
    - net_amount: 净额（元）
    - change_pct/turnover_rate: 涨跌幅/换手率
    """
    ts_code = _normalize_to_ts_code(symbol)
    bare_code = ts_code.split(".")[0] if "." in ts_code else ts_code.lstrip("SHEZBJ").lower()

    # ── 优先：东财实时个股接口（7×24 可用，盘后返回收盘快照）──
    flow = _query_stock_flow(ts_code)
    if flow:
        # ── 计算资金效率指数 ──
        capital_efficiency = None
        try:
            main_pct_val = float(flow.get("main_pct", "0").replace("%", ""))
            chg_val = abs(float(flow.get("change_pct", "0").replace("%", "")))
            if chg_val > 0.001:
                capital_efficiency = round(main_pct_val / chg_val, 2)
        except (ValueError, TypeError):
            pass

        return ThsMoneyflowResponse(
            symbol=ts_code, name=flow["name"],
            price=flow["price"], change_pct=flow["change_pct"],
            turnover_rate=flow.get("turnover_rate", ""),
            inflow=0, outflow=0,
            net_amount=flow["net_amount"],
            main_net=flow["main_net"], main_pct=flow["main_pct"],
            lg_net=flow["lg_net"], lg_pct=flow["lg_pct"],
            md_net=flow["md_net"], md_pct=flow["md_pct"],
            sm_net=flow["sm_net"], sm_pct=flow["sm_pct"],
            xs_net=flow["xs_net"], xs_pct=flow["xs_pct"],
            d5_main_net=flow.get("d5_main_net",0), d5_main_pct=flow.get("d5_main_pct",""),
            d5_lg_net=flow.get("d5_lg_net",0), d5_lg_pct=flow.get("d5_lg_pct",""),
            d5_md_net=flow.get("d5_md_net",0), d5_md_pct=flow.get("d5_md_pct",""),
            d5_sm_net=flow.get("d5_sm_net",0), d5_sm_pct=flow.get("d5_sm_pct",""),
            d5_xs_net=flow.get("d5_xs_net",0), d5_xs_pct=flow.get("d5_xs_pct",""),
            d10_main_net=flow.get("d10_main_net",0), d10_main_pct=flow.get("d10_main_pct",""),
            d10_lg_net=flow.get("d10_lg_net",0), d10_lg_pct=flow.get("d10_lg_pct",""),
            d10_md_net=flow.get("d10_md_net",0), d10_md_pct=flow.get("d10_md_pct",""),
            d10_sm_net=flow.get("d10_sm_net",0), d10_sm_pct=flow.get("d10_sm_pct",""),
            d10_xs_net=flow.get("d10_xs_net",0), d10_xs_pct=flow.get("d10_xs_pct",""),
            capital_efficiency=capital_efficiency,
            source="eastmoney_stock_get", updated_at=datetime.now(),
        )

    # ── 降级：Tushare 日频（moneyflow_dc）──
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()
        pro = _get_tushare_pro()
        from datetime import timedelta
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        df = pro.moneyflow_dc(ts_code=ts_code, start_date=start_date, end_date=end_date)
        if df is not None and not df.empty:
            # 按交易日降序，取最新一条
            df = df.sort_values('trade_date', ascending=False)
            row = df.iloc[0]
            logger.info(f"[moneyflow] Tushare 降级: {ts_code}, trade_date={row.get('trade_date')}, "
                        f"net_mf_amount={row.get('net_mf_amount')}, columns={list(df.columns)}")
            def _f(col: str, default=0.0) -> float:
                v = row.get(col)
                return float(v) if v is not None and v != '' else default
            def _pct(col: str) -> str:
                v = row.get(col)
                if v is not None and v != '' and v != 0:
                    return str(round(float(v), 2))
                return "0"
            # moneyflow_dc 字段单位：万元 → 元（net=买入-卖出）
            lg_net = (_f("buy_elg_amount") - _f("sell_elg_amount")) * 10000
            md_net = (_f("buy_lg_amount") - _f("sell_lg_amount")) * 10000
            sm_net = (_f("buy_md_amount") - _f("sell_md_amount")) * 10000
            xs_net = (_f("buy_sm_amount") - _f("sell_sm_amount")) * 10000
            # net_mf_amount 优先，不存在则用买卖差额计算
            main_net_raw = row.get("net_mf_amount")
            if main_net_raw is None or main_net_raw == '':
                main_net = lg_net + md_net  # 主力=超大单+大单净额
            else:
                main_net = float(main_net_raw) * 10000

            # ── 计算资金效率指数 ──
            capital_efficiency = None
            try:
                main_pct_val = float(row.get("net_amount_rate", 0) or 0)
                chg_val = abs(float(row.get("pct_change", 0) or 0))
                if chg_val > 0.001:
                    capital_efficiency = round(main_pct_val / chg_val, 2)
            except (ValueError, TypeError):
                pass

            return ThsMoneyflowResponse(
                symbol=ts_code,
                name=str(row.get("name", "") or ""),
                price=round(_f("close"), 2),
                change_pct=str(round(_f("pct_change"), 2)) + "%",
                turnover_rate="0%",
                inflow=0,
                outflow=0,
                net_amount=main_net,
                main_net=main_net, main_pct=_pct("net_amount_rate"),
                lg_net=lg_net, lg_pct=_pct("buy_elg_amount_rate"),
                md_net=md_net, md_pct=_pct("buy_lg_amount_rate"),
                sm_net=sm_net, sm_pct=_pct("buy_md_amount_rate"),
                xs_net=xs_net, xs_pct=_pct("buy_sm_amount_rate"),
                capital_efficiency=capital_efficiency,
                source="tushare",
                updated_at=datetime.now(),
            )
    except Exception as e:
        print(f"[Tushare] moneyflow 降级也失败: {e}", flush=True)
        import traceback; traceback.print_exc()

    raise HTTPException(status_code=503, detail=f"获取 {ts_code} 资金流向失败（东方财富+ Tushare 均不可用）")


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
   数据源: Tushare stk_factor_pro 接口。

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

        pro = _get_tushare_pro()

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
            fields='ts_code,trade_date,close,macd_qfq,macd_dif_qfq,macd_dea_qfq,kdj_qfq,kdj_k_qfq,kdj_d_qfq,rsi_qfq_6,rsi_qfq_12,rsi_qfq_24,boll_upper_qfq,boll_mid_qfq,boll_lower_qfq,atr_qfq,cci_qfq,wr_qfq',
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
                macd=float(row.get("macd_qfq", 0) or 0),
                macd_dif=float(row.get("macd_dif_qfq", 0) or 0),
                macd_dea=float(row.get("macd_dea_qfq", 0) or 0),
                kdj=float(row.get("kdj_qfq", 0) or 0),
                kdj_k=float(row.get("kdj_k_qfq", 0) or 0),
                kdj_d=float(row.get("kdj_d_qfq", 0) or 0),
                rsi_6=float(row.get("rsi_qfq_6", 0) or 0),
                rsi_12=float(row.get("rsi_qfq_12", 0) or 0),
                rsi_24=float(row.get("rsi_qfq_24", 0) or 0),
                boll_upper=float(row.get("boll_upper_qfq", 0) or 0),
                boll_mid=float(row.get("boll_mid_qfq", 0) or 0),
                boll_lower=float(row.get("boll_lower_qfq", 0) or 0),
                atr=float(row.get("atr_qfq", 0) or 0),
                cci=float(row.get("cci_qfq", 0) or 0),
                wr=float(row.get("wr_qfq", 0) or 0),
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

        pro = _get_tushare_pro()

        ts_code = _normalize_to_ts_code(symbol)

        from datetime import datetime as dt, timedelta
        if not end_date:
            end_date = dt.now().strftime("%Y%m%d")
        if not start_date:
            start_date = (dt.now() - timedelta(days=90)).strftime("%Y%m%d")

        df = ts.pro_bar(
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


# ========== 概念板块行情 API (数据源: 东财 push2 实时 + Tushare moneyflow_ind_dc 兜底) ==========

@router.get("/concept-fund-flow")
async def get_concept_fund_flow(
    limit: int = Query(15, ge=1, le=100, description="返回数量上限"),
    sort_by: str = Query("pct_change", description="排序字段: pct_change(涨幅) / main_net(主力净流入)"),
):
    """
    获取概念板块实时行情排行，支持按涨幅或主力资金流向排序。

    数据源（优先级）:
    1. 东财 push2 实时接口（盘中实时更新，含涨跌幅+资金拆分明细+板块广度+领涨股）
    2. Tushare moneyflow_ind_dc（降级兜底，日频盘后数据，含概念板块资金拆分明细）

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

    # ── 判断是否应该跳过东财实时接口（9:30前 / 16:00后 / 周末） ──
    now = dt.now()
    before_market = now.hour < 9 or (now.hour == 9 and now.minute < 30)
    skip_em = before_market or now.hour >= 16 or now.weekday() >= 5
    if skip_em:
        logger.info(f"[concept-fund-flow] {'盘前' if before_market else '已过16:00或非交易日'}，跳过东财实时接口，走 Tushare")

    # ── 优先：东财 push2 实时接口（仅在 16:00 前尝试） ──
    if _EM_FLOW_AVAILABLE and not skip_em:
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
                # ── 计算信号强度标签 ──
                main_net_val = fd.get("main_net", 0)
                if main_net_val >= 400000:
                    signal_level = "⚡极端"
                elif main_net_val >= 150000:
                    signal_level = "🔥偏强"
                else:
                    signal_level = "📊常规"

                item = {
                    "name": fd["name"],
                    "code": fd["code"],
                    "pct_change": fd["pct_change"],
                    "main_net": main_net_val,
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
                    "signal_level": signal_level,
                    # Tushare 字段填充空值
                    "vol": 0, "amount": 0, "turnover_rate": 0, "ts_code": "",
                }
                sectors.append(item)

            logger.info(f"[concept-fund-flow] 东财实时: {len(sectors)} 个概念 (sort={sort_by})")
            if sectors:
                return {
                    "sectors": sectors,
                    "count": len(sectors),
                    "sort_by": sort_by,
                    "data_source": "东财push2(realtime)",
                    "trade_date": dt.now().strftime("%Y%m%d"),
                }
            # 东财返回空 → 降级到缓存 → Tushare
            logger.info("[concept-fund-flow] 东财返回 0 条，尝试缓存")
        except Exception as e:
            logger.warning(f"[concept-fund-flow] 东财实时获取失败: {e}")

    # ── 降级：持久化缓存（东财失败时，读上一次成功的数据） ──
    if not skip_em:
        try:
            from core.utils.eastmoney_cache import get_em_cache
            em = get_em_cache()
            cached, meta = em.load_with_fallback("sector_flow", subtype="concept")
            if meta.get("from_cache") and cached:
                aged = em.get_aged_minutes("sector_flow", subtype="concept")
                sectors = []
                for item in cached[:limit]:
                    # ── 信号强度标签 ──
                    cached_main_net = item.get("main_net", 0)
                    if cached_main_net >= 400000:
                        cached_signal = "⚡极端"
                    elif cached_main_net >= 150000:
                        cached_signal = "🔥偏强"
                    else:
                        cached_signal = "📊常规"

                    sectors.append({
                        "name": item.get("name", ""),
                        "code": item.get("code", ""),
                        "pct_change": item.get("pct_change", 0),
                        "main_net": cached_main_net,
                        "main_net_rate": item.get("main_net_rate", 0),
                        "super_large_net": item.get("super_large_net", 0),
                        "large_net": item.get("large_net", 0),
                        "medium_net": item.get("medium_net", 0),
                        "small_net": item.get("small_net", 0),
                        "advancing": item.get("advancing", 0),
                        "declining": item.get("declining", 0),
                        "total_stocks": item.get("total_stocks", 0),
                        "lead_stock_name": item.get("lead_stock_name", ""),
                        "lead_stock_code": item.get("lead_stock_code", ""),
                        "flow_nature": item.get("flow_nature", "平衡"),
                        "signal_level": cached_signal,
                        "vol": 0, "amount": 0, "turnover_rate": 0, "ts_code": "",
                    })
                logger.info(f"[concept-fund-flow] 缓存: {len(sectors)} 个概念 (约{aged:.0f}分钟前)")
                return {
                    "sectors": sectors,
                    "count": len(sectors),
                    "sort_by": sort_by,
                    "data_source": f"东财缓存({aged:.0f}分钟前)",
                    "trade_date": meta.get("cached_at", dt.now().strftime("%Y%m%d"))[:10],
                }
        except Exception:
            pass

    # ── 降级：Tushare moneyflow_ind_dc 日频盘后数据（含资金拆分明细）──
    try:
        from app.config import get_settings
        settings = get_settings()
        token = settings.get_tushare_token()

        pro = _get_tushare_pro()

        from datetime import timedelta

        sectors = []
        trade_date = ""

        # moneyflow_ind_dc 每天盘后更新，盘中走最近已收盘交易日
        is_intraday = now.hour < 15 or (now.hour == 15 and now.minute < 15)
        start_offset = 1 if is_intraday else 0

        for offset in range(start_offset, start_offset + 4):
            attempt_date = (now - timedelta(days=offset)).strftime("%Y%m%d")
            attempt_dt = now - timedelta(days=offset)
            if attempt_dt.weekday() >= 5:
                continue
            try:
                df = pro.moneyflow_ind_dc(
                    trade_date=attempt_date,
                    content_type='概念',
                )
                if df is not None and not df.empty:
                    trade_date = attempt_date

                    # 排序
                    if sort_by == "main_net":
                        df = df.sort_values('net_amount', ascending=False)
                    else:
                        df = df.sort_values('pct_change', ascending=False)

                    for _, row in df.head(limit).iterrows():
                        # moneyflow_ind_dc 返回字段单位为元，统一转为万元（与东财实时数据一致）
                        main_net_val = round(float(row.get("net_amount", 0) or 0) / 10000, 2)
                        main_net_rate_val = round(float(row.get("net_amount_rate", 0) or 0), 2)

                        # ── 信号强度标签 ──
                        if main_net_val >= 400000:
                            ts_signal = "⚡极端"
                        elif main_net_val >= 150000:
                            ts_signal = "🔥偏强"
                        else:
                            ts_signal = "📊常规"

                        sectors.append({
                            "name": str(row["name"]),
                            "ts_code": str(row["ts_code"]),
                            "code": str(row["ts_code"]),
                            "pct_change": round(float(row.get("pct_change", 0) or 0), 2),
                            # 资金拆分明细（元→万元）
                            "main_net": main_net_val,
                            "main_net_fmt": _fmt_net_amount(main_net_val),
                            "main_net_rate": main_net_rate_val,
                            "super_large_net": round(float(row.get("buy_elg_amount", 0) or 0) / 10000, 2),
                            "super_large_net_rate": round(float(row.get("buy_elg_amount_rate", 0) or 0), 2),
                            "large_net": round(float(row.get("buy_lg_amount", 0) or 0) / 10000, 2),
                            "large_net_rate": round(float(row.get("buy_lg_amount_rate", 0) or 0), 2),
                            "medium_net": round(float(row.get("buy_md_amount", 0) or 0) / 10000, 2),
                            "medium_net_rate": round(float(row.get("buy_md_amount_rate", 0) or 0), 2),
                            "small_net": round(float(row.get("buy_sm_amount", 0) or 0) / 10000, 2),
                            "small_net_rate": round(float(row.get("buy_sm_amount_rate", 0) or 0), 2),
                            # moneyflow_ind_dc 无法提供板块广度/成分股
                            "advancing": 0,
                            "declining": 0,
                            "total_stocks": 0,
                            "lead_stock_name": str(row.get("buy_sm_amount_stock", "") or ""),
                            "lead_stock_code": "",
                            "flow_nature": classify_flow_nature(main_net_val, main_net_rate_val),
                            "signal_level": ts_signal,
                            # 量价数据
                            "vol": 0,
                            "amount": 0,
                            "turnover_rate": 0,
                        })
                    break
            except Exception:
                continue

        return {
            "sectors": sectors,
            "count": len(sectors),
            "sort_by": sort_by,
            "trade_date": trade_date,
            "data_source": "Tushare(moneyflow_ind_dc·盘后)",
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

    # ── 实时数据（东财 push2 ulist.np，走 FRP 代理）──
    # 9:30前 / 16:00后 / 周末跳过实时，走 Tushare
    now = dt.now()
    before_mkt = now.hour < 9 or (now.hour == 9 and now.minute < 30)
    skip_em = before_mkt or now.hour >= 16 or now.weekday() >= 5
    if source in ("auto", "realtime") and not trade_date and _EM_FLOW_AVAILABLE and not skip_em:
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
                # 持久化缓存
                try:
                    from core.utils.eastmoney_cache import get_em_cache
                    get_em_cache().save("market_moneyflow", result)
                except Exception:
                    pass
                return result
        except Exception as e:
            logger.warning(f"[moneyflow-mkt] 东财实时获取失败: {e}")
            # 降级：读持久化缓存
            try:
                from core.utils.eastmoney_cache import get_em_cache
                cache = get_em_cache()
                cached, meta = cache.load_with_fallback("market_moneyflow")
                if meta.get("from_cache"):
                    aged = cache.get_aged_minutes("market_moneyflow")
                    logger.warning(f"[moneyflow-mkt] 降级使用缓存 (约{aged:.0f}分钟前)")
                    return cached
            except Exception:
                pass

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

        import pandas as pd
        from datetime import datetime as dt, timedelta

        pro = _get_tushare_pro()

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


# ========== 市场状态诊断仪表盘 ==========

# 申万一级行业指数代码（10个代表性行业）
_SW_SECTOR_CODES = [
    "801080.SI",  # 电子
    "801180.SI",  # 医药生物
    "801120.SI",  # 食品饮料
    "801750.SI",  # 计算机
    "801730.SI",  # 电力设备
    "801880.SI",  # 汽车
    "801050.SI",  # 有色金属
    "801760.SI",  # 传媒
    "801150.SI",  # 银行
    "801230.SI",  # 综合
]

_SW_SECTOR_NAMES = {
    "801080.SI": "电子", "801180.SI": "医药生物", "801120.SI": "食品饮料",
    "801750.SI": "计算机", "801730.SI": "电力设备", "801880.SI": "汽车",
    "801050.SI": "有色金属", "801760.SI": "传媒", "801150.SI": "银行",
    "801230.SI": "综合",
}


@router.get("/market-diagnosis")
async def get_market_diagnosis():
    """
    市场状态诊断仪表盘 V2.0。

    五大指标：
      ① 市场平均振幅 — 成交额前100活跃股近10天平均振幅
      ② 连阳/连阴天数 — 上证指数最大连续同向天数
      ③ 板块轮动速度 — 近5天申万行业涨幅前3是否重复
      ④ 涨跌停比 — 涨停/跌停家数比
      ⑤ MA5方向 — 大盘MA5角度（降权，仅参考）

    综合诊断 → 趋势市 / 震荡市 / 极端市 + 对应策略建议
    """
    import math
    from datetime import datetime as dt, timedelta
    import pandas as pd

    pro = _get_tushare_pro()
    end_date = dt.now().strftime("%Y%m%d")
    start_date = (dt.now() - timedelta(days=30)).strftime("%Y%m%d")
    start_date_10d = (dt.now() - timedelta(days=15)).strftime("%Y%m%d")

    # ── 获取上证指数日线 ──
    df_sh = pro.index_daily(ts_code='000001.SH', start_date=start_date, end_date=end_date)
    if df_sh is None or df_sh.empty:
        raise HTTPException(status_code=503, detail="无法获取上证指数数据")
    df_sh = df_sh.sort_values("trade_date")
    if len(df_sh) < 5:
        raise HTTPException(status_code=503, detail="上证指数数据不足")

    closes = [float(c) for c in df_sh['close'].values]
    data_trade_date = str(df_sh.iloc[-1]['trade_date'])  # Tushare 最新数据日期（盘前可能是昨天）
    execution_date = dt.now().strftime("%Y%m%d")  # 诊断执行日期（用于存储和查询）
    close_latest = closes[-1]

    # ── ① 市场平均振幅：成交额前100活跃股的近10天平均振幅 ──
    avg_amplitude = 0.0
    top100_detail = ""
    try:
        # 获取最新交易日全市场成交额排名
        df_amount = pro.daily(trade_date=data_trade_date, fields='ts_code,amount')
        if df_amount is not None and not df_amount.empty:
            top100 = df_amount.sort_values('amount', ascending=False).head(100)
            top_codes = top100['ts_code'].tolist()

            # 分批查询近10天K线（每批30只）
            all_amplitudes = []
            batch_size = 30
            for i in range(0, len(top_codes), batch_size):
                batch = top_codes[i:i + batch_size]
                try:
                    df_batch = pro.daily(ts_code=','.join(batch),
                                         start_date=start_date_10d, end_date=end_date)
                    if df_batch is not None and not df_batch.empty:
                        for code in batch:
                            sdf = df_batch[df_batch['ts_code'] == code]
                            if len(sdf) >= 5:
                                h = float(sdf['high'].max())
                                l = float(sdf['low'].min())
                                if l > 0:
                                    all_amplitudes.append((h - l) / l * 100)
                except Exception:
                    continue

            if all_amplitudes:
                avg_amplitude = sum(all_amplitudes) / len(all_amplitudes)
                top100_detail = f"({len(all_amplitudes)}只有效股票)"
    except Exception as e:
        logger.warning(f"指标① 活跃股振幅计算失败: {e}")

    # 降级：用上证指数振幅代替
    if avg_amplitude == 0:
        sh_10 = df_sh.tail(10)
        h10 = float(sh_10['high'].max())
        l10 = float(sh_10['low'].min())
        avg_amplitude = (h10 - l10) / l10 * 100 if l10 > 0 else 0
        top100_detail = "(降级：使用上证指数振幅)"

    # ── ② 连阳/连阴天数 ──
    max_up = 0
    max_down = 0
    cur_up = 0
    cur_down = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            cur_up += 1
            cur_down = 0
        elif closes[i] < closes[i - 1]:
            cur_down += 1
            cur_up = 0
        max_up = max(max_up, cur_up)
        max_down = max(max_down, cur_down)
    max_consecutive = max(max_up, max_down)

    # ── ③ 板块轮动速度：申万行业指数 ──
    rotation_speed = 0
    rotation_label = "无数据"
    sector_top_history = []
    try:
        sector_codes_str = ",".join(_SW_SECTOR_CODES)
        df_sectors = pro.index_daily(ts_code=sector_codes_str,
                                     start_date=start_date, end_date=end_date)
        if df_sectors is not None and not df_sectors.empty:
            df_sectors = df_sectors.sort_values(["ts_code", "trade_date"])
            last_dates = sorted(df_sectors['trade_date'].unique())[-5:]
            if len(last_dates) >= 3:
                daily_tops = []
                for td in last_dates:
                    day_data = df_sectors[df_sectors['trade_date'] == td]
                    day_sec = day_data[day_data['ts_code'].isin(_SW_SECTOR_CODES)]
                    if day_sec.empty or 'pct_chg' not in day_sec.columns:
                        continue
                    ranked = day_sec.sort_values("pct_chg", ascending=False)
                    top3 = [str(r['ts_code']) for _, r in ranked.head(3).iterrows()]
                    top3_names = [_SW_SECTOR_NAMES.get(c, c) for c in top3]
                    daily_tops.append({"date": str(td), "top3": top3, "top3_names": top3_names})

                if daily_tops:
                    all_top_codes = set()
                    for d in daily_tops:
                        all_top_codes.update(d['top3'])
                    unique_count = len(all_top_codes)
                    max_unique = min(len(daily_tops) * 3, len(_SW_SECTOR_CODES))
                    rotation_speed = unique_count / max(max_unique, 1)
                    if rotation_speed >= 0.7:
                        rotation_label = "快速轮动"
                    elif rotation_speed >= 0.4:
                        rotation_label = "中等轮动"
                    else:
                        rotation_label = "同一批霸榜"
                    sector_top_history = daily_tops
    except Exception as e:
        logger.warning(f"指标③ 板块轮动计算失败: {e}")

    # ── ④ 涨跌停比例 ──
    limit_up = 0
    limit_down = 0
    try:
        limit_df = pro.limit_list_d(trade_date=data_trade_date, limit_type='U,D')
        if limit_df is not None and len(limit_df) > 0 and 'limit' in limit_df.columns:
            limit_up = int((limit_df['limit'] == 'U').sum())
            limit_down = int((limit_df['limit'] == 'D').sum())
    except Exception:
        pass
    limit_ratio = limit_up / limit_down if limit_down > 0 else (99 if limit_up > 0 else 1)

    # ── ⑤ MA5方向（降权，仅参考）──
    ma5_values = []
    for i in range(len(closes)):
        if i >= 4:
            ma5_values.append(sum(closes[i - 4:i + 1]) / 5)

    ma5_direction = "走平"
    ma5_angle = 0.0
    if len(ma5_values) >= 3:
        n = len(ma5_values)
        x_avg = (n - 1) / 2
        y_avg = sum(ma5_values) / n
        num = sum((i - x_avg) * (ma5_values[i] - y_avg) for i in range(n))
        den = sum((i - x_avg) ** 2 for i in range(n))
        if den > 0 and y_avg > 0:
            slope = num / den
            ma5_angle = math.degrees(math.atan(slope / y_avg))
            if ma5_angle > 30:
                ma5_direction = "向上陡峭"
            elif ma5_angle > 15:
                ma5_direction = "向上"
            elif ma5_angle < -30:
                ma5_direction = "向下陡峭"
            elif ma5_angle < -15:
                ma5_direction = "向下"

    # ── 综合诊断（加权投票制，总票数6.5票）──
    # ① 2票  ② 1票  ③ 2票  ④ 1票  ⑤ 0.5票
    osc = 0.0  # 震荡票（浮点）
    trd = 0.0  # 趋势票（浮点）
    detail_list = []

    # ① 振幅（2票）：>25% → 震荡，<15% → 趋势，15%-25% → 各1票
    amp_signal = "震荡" if avg_amplitude > 25 else ("趋势" if avg_amplitude < 15 else "震荡")
    if avg_amplitude > 25:
        osc += 2
        detail_list.append(f"① 平均振幅{avg_amplitude:.1f}%>25% → 震荡 +2票{top100_detail}")
    elif avg_amplitude < 15:
        trd += 2
        detail_list.append(f"① 平均振幅{avg_amplitude:.1f}%<15% → 趋势 +2票{top100_detail}")
    else:
        osc += 1
        trd += 1
        detail_list.append(f"① 平均振幅{avg_amplitude:.1f}% 居中 → 各+1票{top100_detail}")

    # ② 连续涨跌（1票）：≤3天 → 震荡，≥5天 → 趋势
    if max_consecutive >= 5:
        trd += 1
        detail_list.append(f"② 最大连续{max_consecutive}天≥5 → 趋势 +1票")
    else:
        osc += 1
        detail_list.append(f"② 最大连续{max_consecutive}天≤3 → 震荡 +1票" if max_consecutive <= 3
                          else f"② 最大连续{max_consecutive}天 → 震荡 +1票")

    # ③ 板块轮动（2票）：前3全换 → 震荡，连续不变 → 趋势，中等 → 各1票
    if rotation_label == "快速轮动":
        osc += 2
        detail_list.append(f"③ 板块轮动{rotation_speed:.0%} 每天全换 → 震荡 +2票")
    elif rotation_label == "同一批霸榜":
        trd += 2
        detail_list.append(f"③ 板块轮动{rotation_speed:.0%} 连续霸榜 → 趋势 +2票")
    else:
        osc += 1
        trd += 1
        detail_list.append(f"③ 板块轮动{rotation_speed:.0%} 中等轮动 → 各+1票")

    # ④ 涨跌停比（1票）：1:5~5:1 → 震荡，>5:1 → 趋势，<1:5 → 极端+震荡
    if limit_down > 0 and limit_up / limit_down < 0.2:  # <1:5
        osc += 1
        detail_list.append(f"④ 涨跌停 {limit_up}↑/{limit_down}↓ = 1:{limit_down//max(limit_up,1)} < 1:5 → 震荡 +1票")
    elif limit_ratio > 5:
        trd += 1
        detail_list.append(f"④ 涨跌停 {limit_up}↑/{limit_down}↓ = {limit_ratio:.1f}:1 >5:1 → 趋势 +1票")
    elif limit_ratio < 0.2:
        trd += 1
        detail_list.append(f"④ 涨跌停 {limit_up}↑/{limit_down}↓ = 1:{1/max(limit_ratio,0.01):.0f} <1:5 → 趋势 +1票")
    else:
        osc += 1
        detail_list.append(f"④ 涨跌停 {limit_up}↑/{limit_down}↓ ≈ {limit_ratio:.1f}:1 在1:5~5:1 → 震荡 +1票")

    # ⑤ MA5方向（0.5票，降权）：>30° → 趋势，走平 → 震荡
    if abs(ma5_angle) > 30:
        trd += 0.5
        detail_list.append(f"⑤ MA5角度{ma5_angle:+.1f}° |角度|>30° → 趋势 +0.5票（降权）")
    elif abs(ma5_angle) < 5:
        osc += 0.5
        detail_list.append(f"⑤ MA5角度{ma5_angle:+.1f}° 走平 → 震荡 +0.5票（降权）")
    else:
        osc += 0.5
        detail_list.append(f"⑤ MA5角度{ma5_angle:+.1f}° → 震荡 +0.5票（降权）")

    # ── 最终判定（总票数6.5，≥3.5票震荡 → 震荡市，否则 → 趋势市）──
    if osc >= 3.5:
        state, label, suggestion = "oscillation", "🟡 震荡市", "60分钟右侧交易，持仓1-3天"
    else:
        state, label, suggestion = "trend", "🟢 趋势市", "日线右侧（MA5>MA20），持仓5-30天"

    result = {
        "trade_date": execution_date,
        "data_source": "tushare_v2",
        "indicators": {
            "amplitude": {
                "value": round(avg_amplitude, 1),
                "source": "top100成交额" if top100_detail and "降级" not in top100_detail else "上证指数",
                "signal": amp_signal,
            },
            "consecutive": {
                "max_up": max_up,
                "max_down": max_down,
                "max_any": max_consecutive,
                "signal": "趋势" if max_consecutive >= 5 else "震荡",
            },
            "sector_rotation": {
                "speed": round(rotation_speed, 2),
                "label": rotation_label,
                "unique_sectors": len(set.union(*[set(d['top3']) for d in sector_top_history])) if sector_top_history else 0,
                "history": sector_top_history,
                "signal": "趋势" if rotation_label == "同一批霸榜" else "震荡",
            },
            "limit_ratio": {
                "limit_up": limit_up,
                "limit_down": limit_down,
                "ratio": round(limit_ratio, 2),
                "signal": "趋势" if limit_ratio > 5 or limit_ratio < 0.2 else "震荡",
            },
            "ma5_direction": {
                "direction": ma5_direction,
                "angle_deg": round(ma5_angle, 1),
                "signal": "趋势" if abs(ma5_angle) > 30 else "震荡",
                "weight": "降权参考",
            },
        },
        "diagnosis": {
            "state": state,
            "label": label,
            "suggestion": suggestion,
            "score": {"oscillation": osc, "trend": trd},
        },
        "details": detail_list,
    }

    # 持久化到数据库
    _save_market_diagnosis(result)

    return result


def _save_market_diagnosis(result: dict):
    """将市场诊断结果保存到 PostgreSQL"""
    import json as _json
    from app.database import SessionLocal
    from app.models.market_orm import MarketDiagnosis
    try:
        db = SessionLocal()
        try:
            d = result["diagnosis"]
            row = db.query(MarketDiagnosis).filter(
                MarketDiagnosis.trade_date == result["trade_date"]
            ).first()
            if row:
                row.state = d["state"]
                row.label = d["label"]
                row.suggestion = d["suggestion"]
                row.score_trend = d["score"]["trend"]
                row.score_oscillation = d["score"]["oscillation"]
                row.score_extreme = d["score"].get("extreme", 0)
                row.indicators_json = _json.dumps(result["indicators"], ensure_ascii=False)
                row.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                db.add(MarketDiagnosis(
                    trade_date=result["trade_date"],
                    state=d["state"],
                    label=d["label"],
                    suggestion=d["suggestion"],
                    score_trend=d["score"]["trend"],
                    score_oscillation=d["score"]["oscillation"],
                    score_extreme=d["score"].get("extreme", 0),
                    indicators_json=_json.dumps(result["indicators"], ensure_ascii=False),
                    created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        import traceback
        err_msg = f"[market-diagnosis] PostgreSQL 保存失败: {e}\n{traceback.format_exc()}"
        logging.getLogger(__name__).warning(err_msg)
        print(err_msg, flush=True)
        try:
            from app.services.qqbot_service import send_qq_notification
            send_qq_notification(f"⚠️ 盘前诊断保存DB失败\n{traceback.format_exc()}")
        except Exception:
            pass


# ========== 市场状态查询 API ==========

@router.get("/market-state")
async def get_market_state():
    """查询当日市场状态（趋势/震荡/极端），从 PostgreSQL 读取缓存的诊断结果"""
    import json as _json
    from app.database import SessionLocal
    from app.models.market_orm import MarketDiagnosis

    today_str = datetime.now().strftime("%Y%m%d")

    try:
        db = SessionLocal()
        try:
            row = db.query(MarketDiagnosis).filter(
                MarketDiagnosis.trade_date == today_str
            ).first()

            if row:
                return {
                    "trade_date": row.trade_date,
                    "state": row.state,
                    "label": row.label,
                    "suggestion": row.suggestion,
                    "score": {
                        "trend": row.score_trend,
                        "oscillation": row.score_oscillation,
                        "extreme": row.score_extreme,
                    },
                    "indicators": _json.loads(row.indicators_json) if row.indicators_json else None,
                }
            else:
                return {"trade_date": today_str, "state": "unknown", "label": "⚪ 未知",
                        "suggestion": "今日尚未执行盘前诊断", "indicators": None}
        finally:
            db.close()
    except Exception as e:
        return {"trade_date": today_str, "state": "error", "label": "⚠️ 错误",
                "suggestion": f"读取失败: {e}", "indicators": None}


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

        import sqlite3
        from datetime import datetime as dt, timedelta

        pro = _get_tushare_pro()

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

    # 9:30前 / 16:00后 / 周末 → 拒绝实时查询
    now = datetime.now()
    if now.hour < 9 or (now.hour == 9 and now.minute < 30) or now.hour >= 16 or now.weekday() >= 5:
        raise HTTPException(status_code=503, detail="东财实时接口仅在交易时段 9:30-16:00 可用")

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


@router.get("/intraday-min")
async def get_intraday_min(
    symbols: str = Query(..., description="股票代码，逗号分隔，如 000001.SZ,600519.SH"),
    freq: str = Query("1min", description="分钟K线周期: 1min/5min/15min/30min/60min"),
):
    """
    获取多只股票今日实时分钟K线（盘中数据）。

    数据源：Tushare rt_min（实时分钟行情，支持批量查询）
    - 单次最多查询约4-5只股票（按1000行上限，240分钟×4≈960行）
    - 返回每只股票的分钟K线序列：时间/开/高/低/收/量/额
    - 震荡市行情下用于监控多只持仓的日内走势、寻找精确入场点
    """
    if freq not in ("1min", "5min", "15min", "30min", "60min"):
        raise HTTPException(status_code=400, detail=f"不支持的K线周期: {freq}，可选 1min/5min/15min/30min/60min")

    ts_codes = [c.strip() for c in symbols.split(",") if c.strip()]
    if not ts_codes:
        raise HTTPException(status_code=400, detail="请提供至少一个股票代码")
    if len(ts_codes) > 10:
        raise HTTPException(status_code=400, detail="单次最多查询10只股票")

    try:
        from app.core.trading._60min_analysis import _call_rt_min_daily_raw

        data = _call_rt_min_daily_raw(ts_codes, freq)
        if not data:
            return {
                "symbols": ts_codes,
                "freq": freq,
                "bars": [],
                "count": 0,
                "data_source": "tushare_rt_min_daily",
                "note": "无数据（非交易时段或代码无效）",
            }

        fields = data.get("fields", [])
        items = data.get("items", [])
        col_map = {name: idx for idx, name in enumerate(fields)}

        # 按 ts_code 分组（原始返回的是 ts_code 字段）
        grouped = {}
        for row in items:
            code = row[col_map.get("ts_code", 0)]
            bar = {
                "time": str(row[col_map.get("trade_time", 1)]),
                "open": float(row[col_map.get("open", 2)]),
                "close": float(row[col_map.get("close", 3)]),
                "high": float(row[col_map.get("high", 4)]),
                "low": float(row[col_map.get("low", 5)]),
                "vol": float(row[col_map.get("vol", 6)]),
                "amount": float(row[col_map.get("amount", 7)]),
            }
            grouped.setdefault(code, []).append(bar)

        # 构建每只股票的摘要
        symbols_data = []
        for code in ts_codes:
            bars = grouped.get(code, [])
            if not bars:
                for k, v in grouped.items():
                    if k.split(".")[0] == code.split(".")[0]:
                        bars = v
                        break

            bars.sort(key=lambda b: b["time"])  # API 返回降序，转为升序
            summary = None
            if bars:
                latest = bars[-1]
                day_high = max(b["high"] for b in bars)
                day_low = min(b["low"] for b in bars)
                day_vol = sum(b["vol"] for b in bars)
                day_amount = sum(b["amount"] for b in bars)
                first_close = bars[0]["close"] if bars else latest["close"]
                change_pct = ((latest["close"] - first_close) / first_close * 100) if first_close else 0
                summary = {
                    "latest_price": latest["close"],
                    "day_high": day_high,
                    "day_low": day_low,
                    "day_vol": day_vol,
                    "day_amount": day_amount,
                    "change_pct": round(change_pct, 2),
                    "bar_count": len(bars),
                }

            # ── 60分钟级别：补充历史数据计算 MA 指标 ──
            indicators = None
            if freq == "60min" and bars:
                try:
                    from app.core.trading._60min_analysis import (
                        _fetch_60min_bars_history, _sma, _calc_macd,
                    )
                    hist_bars = _fetch_60min_bars_history(code)
                    if hist_bars:
                        merged = {b["time"]: b for b in hist_bars}
                        for b in bars:
                            merged[b["time"]] = b
                        all_bars = sorted(merged.values(), key=lambda b: b["time"])
                    else:
                        all_bars = bars
                    closes = [b["close"] for b in all_bars]
                    n = len(closes)
                    mas = {}
                    for period in [5, 10, 20, 30, 60]:
                        if n >= period:
                            mas[f"ma{period}"] = round(_sma(closes, period)[-1], 4)
                    macd = _calc_macd(closes) if n >= 26 else None
                    if mas or macd:
                        indicators = {"mas": mas, "bar_count": n}
                        if macd:
                            indicators["macd"] = {
                                "dif": round(macd["dif_latest"], 4),
                                "dea": round(macd["dea_latest"], 4),
                                "bar": round(macd["bar_latest"], 4),
                            }
                except Exception as e:
                    logger.debug(f"[intraday-min] MA计算跳过 {code}: {e}")

            symbols_data.append({
                "code": code,
                "bars": bars[-60:],
                "summary": summary,
                "indicators": indicators,
            })

        return {
            "symbols": ts_codes,
            "freq": freq,
            "symbols_data": symbols_data,
            "total_bars": sum(len(d["bars"]) for d in symbols_data),
            "data_source": "tushare_rt_min_daily",
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[intraday-min] 获取分钟K线失败: {e}")
        raise HTTPException(status_code=500, detail=f"获取分钟K线失败: {str(e)}")
