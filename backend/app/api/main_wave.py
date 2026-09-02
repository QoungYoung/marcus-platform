# -*- coding: utf-8 -*-
"""
主升浪五维评价 API
====================
把 core/main_wave_analyzer.py 的评价选股引擎暴露为 HTTP 接口：

  GET /api/v1/analyze/main-wave/{symbol}        单票完整评价（含 Markdown）
  GET /api/v1/analyze/main-wave/compare         多票横向对比
  GET /api/v1/analyze/main-wave/candidates      涨停池选股（按评分排序）
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/analyze", tags=["Main Wave Analyzer"])


def _get_analyzer():
    try:
        from core.main_wave_analyzer import (
            DataFetcher, analyze_stock, compare_stocks, score_candidates,
        )
        return DataFetcher, analyze_stock, compare_stocks, score_candidates
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"分析引擎不可用: {e!r}")


@router.get("/main-wave/compare")
def main_wave_compare(symbols: str = Query(..., description="逗号分隔：600613,002412"),
                      as_of: Optional[str] = Query(None),
                      days: int = Query(120, ge=30, le=250)):
    """多票横向对比 + 排序。"""
    _fetcher, analyze_stock, compare_stocks, _s = _get_analyzer()
    syms = [s.strip() for s in symbols.split(",") if s.strip()]
    if not syms:
        raise HTTPException(status_code=422, detail="symbols 不能为空")
    f = _fetcher()
    analyses = [analyze_stock(s, fetcher=f, as_of=as_of, days=days) for s in syms]
    comp = compare_stocks(analyses)
    return {"as_of": as_of, "result": comp,
            "analyses": [
                {k: a.get(k) for k in ("symbol", "name", "industry", "score", "verdict",
                                        "structure", "five", "moneyflow", "fundamental")
                 if k in a} for a in analyses]}


@router.get("/main-wave/candidates")
def main_wave_candidates(limit: int = Query(10, ge=1, le=30),
                         as_of: Optional[str] = Query(None),
                         workers: int = Query(4, ge=1, le=8)):
    """涨停池选股：取当日连板数优先的 N 只，评分排序。"""
    _fetcher, analyze_stock, compare_stocks, score_candidates = _get_analyzer()
    f = _fetcher()
    try:
        pool = f.zt_pool(as_of)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"涨停池获取失败: {e!r}")
    if not pool:
        raise HTTPException(status_code=404, detail="涨停池为空（akshare 不可用或当日无数据）")
    pool = [p for p in pool if "ST" not in p.get("name", "") and p.get("pct_chg", 0) > 0]
    pool.sort(key=lambda p: (p.get("limit_times", 0), p.get("amount", 0)), reverse=True)
    symbols = [p["code"] for p in pool[:limit]]
    results = score_candidates(symbols, fetcher=f, as_of=as_of, max_workers=workers)
    comp = compare_stocks(results)
    return {"mode": "candidates", "as_of": as_of,
            "pool_size": len(pool), "evaluated": len(symbols), "result": comp}


@router.get("/main-wave/{symbol}")
def main_wave_detail(symbol: str,
                     as_of: Optional[str] = Query(None, description="截止交易日 YYYYMMDD"),
                     days: int = Query(120, ge=30, le=250)):
    """单票完整「主升浪评价」（含 Markdown 报告与评分）。"""
    _fetcher, analyze_stock, _c, _s = _get_analyzer()
    try:
        a = analyze_stock(symbol, as_of=as_of, days=days)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"分析失败: {e!r}")
    if a.get("error"):
        raise HTTPException(status_code=404, detail=a["error"])
    # 去掉超大字段，保留结构化结果
    a = {k: v for k, v in a.items() if k not in ("bars_all",)}
    return a
