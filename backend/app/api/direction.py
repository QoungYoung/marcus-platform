# -*- coding: utf-8 -*-
"""方向预测 API 端点。"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, Query

router = APIRouter(prefix="/direction", tags=["direction"])

_prediction_service = None
_service_lock = threading.Lock()
EM_PROXY_URL = os.environ.get("EM_PROXY_URL", "")


def _safe_float(v):
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0


def _get_service():
    global _prediction_service
    if _prediction_service is None:
        with _service_lock:
            if _prediction_service is None:
                from app.services.direction_prediction import DirectionPredictionService
                _prediction_service = DirectionPredictionService()
    return _prediction_service


def _fetch_daily_bars(pro, symbols: List[str], end_date: str,
                      lookback_days: int = 40) -> Dict[str, List[dict]]:
    """获取各股票最近 N 根日线。"""
    start_dt = datetime.strptime(end_date, "%Y%m%d") - timedelta(days=lookback_days)
    start_date = start_dt.strftime("%Y%m%d")
    result: Dict[str, List[dict]] = {}

    try:
        for i in range(0, len(symbols), 100):
            batch = ",".join(symbols[i:i + 100])
            df = pro.daily(ts_code=batch, start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                continue
            df = df.sort_values("trade_date")
            for ts_code, group in df.groupby("ts_code"):
                result[str(ts_code)] = [
                    {
                        "close": float(r.get("close", 0) or 0),
                        "open": float(r.get("open", 0) or 0),
                        "vol": float(r.get("vol", 0) or 0),
                        "amount": float(r.get("amount", 0) or 0),
                    }
                    for _, r in group.iterrows()
                ]
    except Exception:
        pass
    return result


def _query_eastmoney_flow(ts_code: str) -> Optional[dict]:
    """查询单只股票东方财富实时资金流向。

    Returns:
        {big_order_net, main_force_ratio, flow_5d_cum, available}
        或 None（查询失败）
    """
    code = ts_code.split(".")[0] if "." in ts_code else ts_code.lstrip("SHEZBJ")
    secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
    fields = ("f12,f14,f2,f3,f170,"
              "f137,f140,f143,f146,f149,f193,f194,f195,f196,f197,"
              "f434,f435,f436,f437,f438,f454,f455,f456,f457,f458")

    try:
        if EM_PROXY_URL:
            import requests as req
            resp = req.get(f"{EM_PROXY_URL}/api/qt/stock/get?secid={secid}&fields={fields}",
                           timeout=10)
            data = resp.json()
        else:
            from curl_cffi import requests as cffi_req
            resp = cffi_req.get("https://push2.eastmoney.com/api/qt/stock/get",
                params={"secid": secid, "fields": fields},
                headers={"User-Agent": "Mozilla/5.0",
                         "Cookie": os.environ.get("EASTMONEY_COOKIE", "")},
                impersonate="chrome124", timeout=10)
            data = resp.json()

        d = data.get("data")
        if not d:
            return None

        main_net = _safe_float(d.get("f137"))          # 主力净额（元）
        main_pct = _safe_float(d.get("f193")) / 100    # 主力净占比（万分比→%）
        d5_main_net = _safe_float(d.get("f434"))       # 5日主力净额（元）

        return {
            "big_order_net": main_net,
            "main_force_ratio": round(main_pct, 2),
            "flow_5d_cum": d5_main_net,
            "available": True,
        }
    except Exception:
        return None


def _fetch_moneyflow_eastmoney(symbols: List[str]) -> Dict[str, dict]:
    """并行查询东方财富个股资金流向。"""
    result: Dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(_query_eastmoney_flow, s): s for s in symbols}
        for future in as_completed(futures, timeout=30):
            try:
                flow = future.result()
                sym = futures[future]
                if flow:
                    result[sym] = flow
            except Exception:
                pass

    return result


def _fetch_moneyflow_tushare(pro, trade_date: str,
                              symbols: List[str]) -> Dict[str, dict]:
    """Tushare moneyflow_dc 降级（金额维度，与东财/训练数据一致）。"""
    result: Dict[str, dict] = {}
    symbol_set = set(symbols)

    try:
        df = pro.moneyflow_dc(trade_date=trade_date)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                ts_code = str(row.get("ts_code", ""))
                if ts_code not in symbol_set:
                    continue
                buy_lg = float(row.get("buy_lg_amount", 0) or 0)
                sell_lg = float(row.get("sell_lg_amount", 0) or 0)
                buy_elg = float(row.get("buy_elg_amount", 0) or 0)
                sell_elg = float(row.get("sell_elg_amount", 0) or 0)
                total_amount = buy_lg + sell_lg + buy_elg + sell_elg + \
                    float(row.get("buy_sm_amount", 0) or 0) + float(row.get("sell_sm_amount", 0) or 0)
                big_order_net = (buy_lg + buy_elg) - (sell_lg + sell_elg)  # 万元
                result[ts_code] = {
                    "big_order_net": big_order_net * 1e4,  # 万元→元，与东财对齐
                    "main_force_ratio": round(big_order_net / total_amount, 4) if total_amount > 0 else 0,
                    "flow_5d_cum": 0,
                    "available": True,
                }
    except Exception:
        pass

    return result


def _moneyflow_fallback(quotes: Dict[str, dict],
                        symbols: List[str]) -> Dict[str, dict]:
    """终极降级：用成交额×涨跌幅作为资金流向代理信号。"""
    result: Dict[str, dict] = {}
    for sym in symbols:
        q = quotes.get(sym, {})
        amount = float(q.get("amount", 0) or 0)
        pct = float(q.get("change_pct", 0) or 0)
        proxy_net = amount * pct / 100  # 成交额(元) × 涨跌幅 → 近似净流入(元)
        result[sym] = {
            "big_order_net": proxy_net,
            "main_force_ratio": round(pct / 10, 3),
            "flow_5d_cum": proxy_net,
            "available": True,
        }
    return result


@router.get("/predict")
async def predict_direction(
    horizon: str = Query("5d", description="预测周期: 1d/3d/5d"),
    date: Optional[str] = Query(None, description="历史日期 YYYYMMDD，不传使用最新"),
    min_confidence: float = Query(0.55, description="最低置信度阈值"),
    limit: int = Query(30, description="返回前N只"),
):
    """预测股票上涨概率。"""
    import pandas as pd
    from app.services.industry_leaderboard import IndustryLeaderboardService
    from app.services.direction_prediction import (
        derive_stock_features,
        add_cross_sectional_ranks,
        compute_market_breadth,
        fetch_index_features,
        _get_data_dir,
        INDEX_COLS,
    )
    from app.core.trading._api_config import get_tushare_pro

    pro = get_tushare_pro()
    svc = _get_service()
    lb = IndustryLeaderboardService()

    # 获取候选股 + 行情
    is_historical = date is not None
    if is_historical:
        candidates, _ = lb._get_industry_candidates_historical(date)
        quotes = lb._historical_quotes([c["ts_code"] for c in candidates], date)
    else:
        candidates = lb._get_industry_candidates()
        quotes, _ = lb._fetch_realtime_quotes_batch(
            [c["ts_code"] for c in candidates]
        )
        date = datetime.now().strftime("%Y%m%d")

    if not candidates:
        return {
            "market_regime": "unknown",
            "confidence_penalty": 0.0,
            "horizon": horizon,
            "predictions": [],
        }

    active_symbols = [c["ts_code"] for c in candidates if c["ts_code"] in quotes]
    if not active_symbols:
        return {
            "market_regime": "unknown",
            "confidence_penalty": 0.0,
            "horizon": horizon,
            "predictions": [],
        }

    # 1. 日线数据（量比/连涨连跌/缺口/RSI/MA20）
    daily_bars = _fetch_daily_bars(pro, active_symbols, date)

    # 2. 资金流向：历史模式走 Tushare，实时模式走东财 → Tushare 降级 → 代理
    if is_historical:
        moneyflow = _fetch_moneyflow_tushare(pro, date, active_symbols)
        if not moneyflow:
            moneyflow = _moneyflow_fallback(quotes, active_symbols)
    else:
        moneyflow = _fetch_moneyflow_eastmoney(active_symbols)
        if not moneyflow:
            moneyflow = _fetch_moneyflow_tushare(pro, date, active_symbols)
        if not moneyflow:
            moneyflow = _moneyflow_fallback(quotes, active_symbols)

    # 3. 推导真实特征
    features_data = {}
    for c in candidates:
        sym = c["ts_code"]
        bars = daily_bars.get(sym, [])
        q = quotes.get(sym, {})
        flow = moneyflow.get(sym)
        features_data[sym] = derive_stock_features(bars, q, flow)

    # 3.5 截面排名
    add_cross_sectional_ranks(features_data)

    features_df = pd.DataFrame.from_dict(features_data, orient="index")

    # 4. 指数衍生特征（上证指数）
    idx_feat = fetch_index_features(pro, date)
    for col in INDEX_COLS:
        features_df[col] = idx_feat.get(col, 0)

    # 5. 市场宽度
    breadth = compute_market_breadth(quotes)

    # 6. 加载已训练模型
    model_path = _get_data_dir() / "direction_model.pkl"
    if model_path.exists():
        import pickle
        with open(model_path, "rb") as f:
            saved = pickle.load(f)
            svc.models = saved.get("models", {})
            svc.scalers = saved.get("scalers", {})
            svc.calibrators = saved.get("calibrators", {})

    # 7. 预测
    predictions = svc.predict(features_df, horizon=horizon, breadth=breadth,
                              index_features=idx_feat)

    # 8. 补充 name/industry
    sym_map = {c["ts_code"]: c for c in candidates}
    enriched = []
    for p in predictions[:limit]:
        if p["confidence"] < min_confidence:
            continue
        info = sym_map.get(p["symbol"], {})
        enriched.append({
            "symbol": p["symbol"],
            "name": info.get("name", ""),
            "industry": info.get("industry", ""),
            "up_probability": p["up_probability"],
            "confidence": p["confidence"],
        })

    return {
        "market_regime": enriched[0]["regime_label"] if enriched else "unknown",
        "confidence_penalty": enriched[0]["confidence_multiplier"] if enriched else 0.0,
        "horizon": horizon,
        "predictions": enriched,
    }


@router.get("/validate")
async def validate_models():
    """返回最近一次走步前进验证的指标。"""
    svc = _get_service()
    if not svc.metrics:
        return {"status": "no_validation_data", "message": "尚未运行走步前进验证"}
    return {
        "status": "ok",
        "metrics": svc.metrics,
        "updated_at": datetime.now().isoformat(),
    }
