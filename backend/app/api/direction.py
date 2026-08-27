# -*- coding: utf-8 -*-
"""方向预测 API 端点 v3 — 行业龙头预期持有期收益率预测。

推理链路（15:01 运行，T 日数据已完成）:
  1. 获取行业龙头候选股 + T 日行情
  2. 资金流向: 东财 → Tushare → T-1 缓存(TTL=1天) → 20 日中位数兜底
  3. 特征推导 (derive_stock_features + add_sector_features)
  4. 加载已训练 XGBRegressor 模型
  5. 预测 expected_return，按行业分散约束 (≤2/行业) 返回 Top-N
"""

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

MAX_PER_INDUSTRY = 2
MONEYFLOW_TTL_DAYS = 1  # 资金流向前向填充有效期
MONEYFLOW_MEDIAN_DAYS = 20  # 中位数兜底窗口


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


# ── 历史行情获取 ──────────────────────────────────────────

def _fetch_daily_bars(pro, symbols: List[str], end_date: str,
                      lookback_days: int = 40) -> Dict[str, List[dict]]:
    """获取各股票最近 N 根日线（截至 end_date）。"""
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


# ── 资金流向获取（v3：前向填充 + TTL + 中位数兜底）──────

def _query_eastmoney_flow(ts_code: str) -> Optional[dict]:
    """查询单只股票东方财富实时资金流向。

    Returns:
        {big_order_net, main_force_ratio, flow_5d_cum, available}
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

        main_net = _safe_float(d.get("f137"))
        main_pct = _safe_float(d.get("f193")) / 100
        d5_main_net = _safe_float(d.get("f434"))

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
    """Tushare moneyflow_dc 降级。"""
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
                big_order_net = (buy_lg + buy_elg) - (sell_lg + sell_elg)
                result[ts_code] = {
                    "big_order_net": big_order_net * 1e4,
                    "main_force_ratio": round(big_order_net / total_amount, 4) if total_amount > 0 else 0,
                    "flow_5d_cum": 0,
                    "available": True,
                }
    except Exception:
        pass

    return result


def _fill_moneyflow_forward(moneyflow: Dict[str, dict],
                            symbols: List[str],
                            date: str) -> Dict[str, dict]:
    """资金流向前向填充 + 20 日中位数兜底。

    策略（优先级）：
      1. 已成功获取 → 直接使用 + 缓存到 TTL
      2. API 失败 → 查 T-1 缓存（TTL=1 天）
      3. 缓存过期 → 查本地历史数据，取该股票最近 20 日中位数
      4. 无历史 → 填 0（与训练数据分布一致）

    无 available 标志，无需 _moneyflow_fallback。
    """
    result = {}
    for sym in symbols:
        if sym in moneyflow:
            result[sym] = moneyflow[sym]
            continue

        # T-1 缓存回退
        cached = _get_moneyflow_from_cache(sym, date)
        if cached is not None:
            result[sym] = cached
            continue

        # 20 日中位数兜底
        median_flow = _get_moneyflow_median(sym, date)
        if median_flow is not None:
            result[sym] = median_flow
            continue

        result[sym] = {
            "big_order_net": 0.0,
            "main_force_ratio": 0.0,
            "flow_5d_cum": 0.0,
            "available": False,
        }

    return result


# 简易内存缓存：{symbol: {date: moneyflow_dict}}
_moneyflow_history: Dict[str, Dict[str, dict]] = {}


def _cache_moneyflow(symbol: str, date: str, data: dict):
    """缓存资金流数据。"""
    if symbol not in _moneyflow_history:
        _moneyflow_history[symbol] = {}
    _moneyflow_history[symbol][date] = data
    # 只保留最近 30 天
    if len(_moneyflow_history[symbol]) > 30:
        oldest = sorted(_moneyflow_history[symbol].keys())[0]
        del _moneyflow_history[symbol][oldest]


def _get_moneyflow_from_cache(symbol: str, date: str) -> Optional[dict]:
    """从缓存获取 T-1 日资金流（TTL=1 天）。"""
    date_dt = datetime.strptime(date, "%Y%m%d")
    for offset in range(1, MONEYFLOW_TTL_DAYS + 1):
        check_dt = date_dt - timedelta(days=offset)
        check_date = check_dt.strftime("%Y%m%d")
        cached = _moneyflow_history.get(symbol, {}).get(check_date)
        if cached:
            return {**cached, "available": True, "_source": "ttl_cache"}
    return None


def _get_moneyflow_median(symbol: str, date: str) -> Optional[dict]:
    """从历史缓存取最近 20 日中位数作为兜底值。"""
    history = _moneyflow_history.get(symbol, {})
    if not history:
        return None

    sorted_dates = sorted(history.keys())
    recent = sorted_dates[-MONEYFLOW_MEDIAN_DAYS:]

    big_nets = [history[d]["big_order_net"] for d in recent]
    ratios = [history[d]["main_force_ratio"] for d in recent]
    cum5ds = [history[d].get("flow_5d_cum", 0) for d in recent]

    if not big_nets:
        return None

    big_nets.sort()
    ratios.sort()
    cum5ds.sort()
    n = len(big_nets)

    return {
        "big_order_net": big_nets[n // 2],
        "main_force_ratio": round(ratios[n // 2], 3),
        "flow_5d_cum": cum5ds[n // 2],
        "available": False,
        "_source": "median_fallback",
    }


def _fetch_all_moneyflow(pro, date: str, symbols: List[str],
                         is_historical: bool) -> Dict[str, dict]:
    """统一资金流获取入口：实时（东财→Tushare）或历史（Tushare），然后前向填充。

    无 _moneyflow_fallback（不生成代理信号）。
    """
    if is_historical:
        moneyflow = _fetch_moneyflow_tushare(pro, date, symbols)
    else:
        moneyflow = _fetch_moneyflow_eastmoney(symbols)
        if not moneyflow:
            moneyflow = _fetch_moneyflow_tushare(pro, date, symbols)

    # 缓存成功获取的数据
    for sym, flow in moneyflow.items():
        _cache_moneyflow(sym, date, flow)

    # 前向填充兜底
    return _fill_moneyflow_forward(moneyflow, symbols, date)


# ── 主端点 ───────────────────────────────────────────────

@router.get("/predict")
def predict_direction(
    horizon: str = Query("5d", description="预测周期: 1d/3d/5d"),
    date: Optional[str] = Query(None, description="历史日期 YYYYMMDD，不传使用最新"),
    min_return: float = Query(-5.0, description="最低预期收益率阈值(%)"),
    limit: int = Query(30, description="返回前N只"),
):
    """预测行业龙头股预期 N 日持有期收益率（XGBoost 回归模型）。

    在 15:01 运行，T 日数据已完整收盘。特征基于 T 日数据计算，
    预测次日（T+1）买入并持有 N 天的预期收益。

    返回按 expected_return 降序排列的龙头股，每行业最多 2 只以确保分散。
    """
    import pandas as pd
    from app.services.industry_leaderboard import IndustryLeaderboardService
    from app.services.direction_prediction import (
        derive_stock_features,
        add_sector_features,
        _get_data_dir,
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
            "market_regime": "transitional",
            "horizon": horizon,
            "predictions": [],
        }

    active_symbols = [c["ts_code"] for c in candidates if c["ts_code"] in quotes]
    if not active_symbols:
        return {
            "market_regime": "transitional",
            "horizon": horizon,
            "predictions": [],
        }

    # 0. 市场状态：复用 leaderboard 的 ADX/MA 判定
    regime = lb._detect_market_regime(as_of_date=date)

    # 1. 日线数据
    daily_bars = _fetch_daily_bars(pro, active_symbols, date)

    # 2. 资金流向：东财 → Tushare → T-1 缓存(TTL=1) → 20 日中位数兜底
    moneyflow = _fetch_all_moneyflow(pro, date, active_symbols, is_historical)

    # 3. 特征推导
    features_data = {}
    for c in candidates:
        sym = c["ts_code"]
        bars = daily_bars.get(sym, [])
        q = quotes.get(sym, {})
        flow = moneyflow.get(sym)
        features_data[sym] = derive_stock_features(bars, q, flow)

    # 4. 行业相对特征（全市场计算后使用）
    industries = {c["ts_code"]: c["industry"] for c in candidates if c.get("industry")}
    add_sector_features(features_data, industries)

    features_df = pd.DataFrame.from_dict(features_data, orient="index")

    # 5. 加载已训练模型
    model_path = _get_data_dir() / "direction_model.pkl"
    if model_path.exists():
        svc.load(model_path)

    # 6. 预测（26 维纯截面特征，不含市场/指数特征）
    predictions = svc.predict(features_df, horizon=horizon)

    # 7. 补充 name/industry + 行业分散约束
    sym_map = {c["ts_code"]: c for c in candidates}
    enriched = []
    industry_counts: Dict[str, int] = {}

    for p in predictions:
        if p["expected_return"] < min_return:
            continue
        info = sym_map.get(p["symbol"], {})
        ind = info.get("industry", "")

        if ind:
            cnt = industry_counts.get(ind, 0)
            if cnt >= MAX_PER_INDUSTRY:
                continue
            industry_counts[ind] = cnt + 1

        enriched.append({
            "symbol": p["symbol"],
            "name": info.get("name", ""),
            "industry": ind,
            "expected_return": p["expected_return"],
        })

        if len(enriched) >= limit:
            break

    return {
        "market_regime": regime,
        "horizon": horizon,
        "predictions": enriched,
    }


@router.get("/validate")
def validate_models():
    """返回最近一次走步前进验证的指标。

    包含回归指标（RMSE, Spearman R, 方向准确率）和纯多头组合指标。
    """
    svc = _get_service()
    if not svc.metrics:
        return {"status": "no_validation_data", "message": "尚未运行走步前进验证"}

    result = {
        "status": "ok",
        "metrics": {},
        "updated_at": datetime.now().isoformat(),
    }

    for horizon in ["1d", "3d", "5d"]:
        if horizon in svc.metrics:
            m = svc.metrics[horizon]
            entry = {
                "rmse_mean": m.get("rmse_mean"),
                "spearman_r_mean": m.get("spearman_r_mean"),
                "direction_accuracy_mean": m.get("direction_accuracy_mean"),
                "n_windows": m.get("n_windows"),
            }
            # 附加纯多头组合指标（如有）
            if horizon in svc.portfolio_metrics:
                pm = svc.portfolio_metrics[horizon]
                entry["portfolio"] = {
                    "information_ratio": pm.get("information_ratio"),
                    "monthly_win_rate": pm.get("monthly_win_rate"),
                    "max_drawdown": pm.get("max_drawdown"),
                    "calmar_ratio": pm.get("calmar_ratio"),
                }
            result["metrics"][horizon] = entry

    return result
