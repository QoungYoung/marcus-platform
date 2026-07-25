#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared technical divergence signal checker.

Used by both stop_loss_monitor.py (Rule 2.5) and marcus_trade.py (trend constraint).
Data sources (triple):
  1. XueqiuEngine → today's live OHLCV (current, high, low, open, volume)
  2. Tushare pro.daily() → historical daily bars (35+ for RSI/KDJ/volume)
  3. Tushare stk_factor_pro() → yesterday's confirmed indicators (MACD/KDJ/RSI/Boll seeds)

Calls core.realtime_indicators.calculate_realtime_indicators() for intraday MACD/KDJ/RSI estimates,
then evaluates the 5 divergence signals with today's estimated + yesterday's confirmed data.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def check_tech_divergence_signals(
    symbol: str,
    current_price: float,
    float_pnl_pct: float,
    cache: Optional[Dict] = None,
    cache_key: Optional[str] = None,
) -> Tuple[List[bool], List[str]]:
    """
    Evaluate 5 technical divergence signals using real-time intraday data.

    Five signals:
      ① MACD红柱连续缩量 (price up, bar shrinking 2+ days)
      ② RSI顶背离 (price near peak, RSI lower than peak RSI)
      ③ 量价背离 (price up, volume declining vs previous period)
      ④ KDJ J > 100 (extreme overbought)
      ⑤ 布林上轨外 (current price > bollinger upper band)

    Args:
        symbol: Stock code (any format, will be normalized)
        current_price: Real-time current price (from Xueqiu/Tencent)
        float_pnl_pct: Current float P&L percentage
        cache: External cache dict for (signals, details) tuples
        cache_key: Cache key string

    Returns:
        (signals, details)
        signals: [bool×5]
        details: [str] descriptions of triggered signals
    """
    # ── Cache check ──
    today_str = datetime.now().strftime('%Y%m%d')
    key = cache_key or f"{symbol}_{today_str}"
    now = time.time()

    if cache is not None and key in cache:
        cached_result, cached_ts = cache[key]
        if now - cached_ts < 3600:
            signals, details = cached_result
            return list(signals), list(details)

    try:
        from app.api.indicator import _normalize_to_ts_code
        from app.config import get_settings

        settings = get_settings()
        token = settings.get_tushare_token()
        if not token:
            return [False] * 5, []

        from app.core.trading._api_config import get_tushare_pro as _get_ts_pro; pro = _get_ts_pro()
        ts_code = _normalize_to_ts_code(symbol)
        end_d = datetime.now().strftime("%Y%m%d")
        start_d = (datetime.now() - timedelta(days=90)).strftime("%Y%m%d")

        # ── Source 1: Today's live quote ──
        realtime_quote = _fetch_realtime_quote(symbol)

        # ── Source 2: Historical daily bars ──
        df_daily = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=40)
        if df_daily is None or df_daily.empty or len(df_daily) < 9:
            return [False] * 5, []
        df_daily = df_daily.sort_values("trade_date", ascending=True)

        # ── Source 3: Yesterday's confirmed indicators ──
        df_tech = pro.stk_factor_pro(
            ts_code=ts_code, start_date=start_d, end_date=end_d,
            fields='trade_date,close,macd_dif_qfq,macd_dea_qfq,'
                   'rsi_qfq_6,kdj_k_qfq,kdj_d_qfq,boll_upper_qfq'
        )
        if df_tech is None or df_tech.empty or len(df_tech) < 20:
            return [False] * 5, []
        df_tech = df_tech.sort_values("trade_date", ascending=True)

        # ── Try realtime path: need live quote with high/low/open ──
        if realtime_quote and realtime_quote.get('current'):
            signals, details = _eval_with_realtime(
                df_tech, df_daily, realtime_quote, current_price
            )
        else:
            # Fallback: stk_factor_pro + daily bars only
            signals, details = _eval_from_historical(
                df_tech, df_daily, current_price
            )

    except Exception as e:
        logger.warning(f"[TechDivergence] 计算失败 {symbol}: {e}")
        return [False] * 5, []

    # ── Cache result ──
    if cache is not None:
        cache[key] = ((signals.copy(), details.copy()), now)

    return signals, details


def _fetch_realtime_quote(symbol: str) -> Optional[Dict]:
    """Fetch today's live quote from XueqiuEngine (Tencent qt.gtimg.cn)."""
    try:
        from workspace_detector import XUEQIU_DIR
        from xueqiu_engine import XueqiuEngine
        xq_config = str(XUEQIU_DIR / "config.json")
        engine = XueqiuEngine(config_file=xq_config)
        return engine.get_stock_quote(symbol)
    except Exception as e:
        logger.debug(f"[TechDivergence] XueqiuEngine不可用: {e}")
        return None


def _eval_with_realtime(
    df_tech, df_daily, realtime_quote: Dict, current_price: float
) -> Tuple[List[bool], List[str]]:
    """Evaluate 5 signals using realtime_indicators + historical data."""
    from core.realtime_indicators import (
        PrevIndicators, DailyBar, calculate_realtime_indicators,
    )

    # Build PrevIndicators from stk_factor_pro
    latest_tech = df_tech.iloc[-1]
    prev_close = float(latest_tech.get("close", 0) or 0)
    prev_kdj_k = float(latest_tech.get("kdj_k_qfq", 0) or 0) or 50.0
    prev_kdj_d = float(latest_tech.get("kdj_d_qfq", 0) or 0) or 50.0
    prev_macd_dif = float(latest_tech.get("macd_dif_qfq", 0) or 0) or 0.0
    prev_macd_dea = float(latest_tech.get("macd_dea_qfq", 0) or 0) or 0.0

    prev_indicators = PrevIndicators(
        trade_date=str(latest_tech["trade_date"]),
        kdj_k=prev_kdj_k,
        kdj_d=prev_kdj_d,
        macd_dea=prev_macd_dea,
        macd_ema12=prev_close + prev_macd_dif if prev_close > 0 and prev_macd_dif else 0.0,
        macd_ema26=prev_close if prev_close > 0 else 0.0,
    )

    # Build DailyBar list
    bars = [
        DailyBar(
            trade_date=str(r["trade_date"]),
            open=float(r["open"]),
            high=float(r["high"]),
            low=float(r["low"]),
            close=float(r["close"]),
            vol=float(r.get("vol", 0) or 0),
        )
        for _, r in df_daily.iterrows()
    ]

    realtime = calculate_realtime_indicators(
        symbol="",
        realtime_quote=realtime_quote,
        historical_bars=bars,
        prev_indicators=prev_indicators,
    )

    return _eval_five_signals(
        closes_hist=[float(v) for v in df_tech['close'].values],
        macd_difs=[float(v) for v in df_tech['macd_dif_qfq'].values],
        macd_deas=[float(v) for v in df_tech['macd_dea_qfq'].values],
        rsi6s=[float(v) for v in df_tech['rsi_qfq_6'].values],
        boll_uppers=[float(v) for v in df_tech['boll_upper_qfq'].values],
        volumes=[float(v) for v in df_daily['vol'].values],
        current_price=current_price,
        today_bar_est=realtime.macd_bar,
        today_rsi_est=realtime.rsi_6,
        today_kdj_j_est=realtime.kdj_j,
    )


def _eval_from_historical(
    df_tech, df_daily, current_price: float
) -> Tuple[List[bool], List[str]]:
    """Fallback: evaluate signals using only confirmed daily data (no live quote)."""
    closes_hist = [float(v) for v in df_tech['close'].values]
    macd_difs = [float(v) for v in df_tech['macd_dif_qfq'].values]
    macd_deas = [float(v) for v in df_tech['macd_dea_qfq'].values]
    rsi6s = [float(v) for v in df_tech['rsi_qfq_6'].values]
    boll_uppers = [float(v) for v in df_tech['boll_upper_qfq'].values]
    volumes = [float(v) for v in df_daily['vol'].values]

    # Use yesterday's confirmed values
    hist_bars = [2.0 * (dif - dea) for dif, dea in zip(macd_difs, macd_deas)]
    yesterday_bar = hist_bars[-1] if hist_bars else 0
    yesterday_rsi = rsi6s[-1] if rsi6s else 50.0
    yesterday_k = float(df_tech['kdj_k_qfq'].values[-1]) if len(df_tech) > 0 else 50.0
    yesterday_d = float(df_tech['kdj_d_qfq'].values[-1]) if len(df_tech) > 0 else 50.0
    yesterday_j = 3.0 * yesterday_k - 2.0 * yesterday_d

    return _eval_five_signals(
        closes_hist=closes_hist,
        macd_difs=macd_difs,
        macd_deas=macd_deas,
        rsi6s=rsi6s,
        boll_uppers=boll_uppers,
        volumes=volumes,
        current_price=current_price,
        today_bar_est=yesterday_bar,    # no estimate, reuse yesterday
        today_rsi_est=yesterday_rsi,
        today_kdj_j_est=yesterday_j,
    )


def _eval_five_signals(
    closes_hist: List[float],
    macd_difs: List[float],
    macd_deas: List[float],
    rsi6s: List[float],
    boll_uppers: List[float],
    volumes: List[float],
    current_price: float,
    today_bar_est: float,
    today_rsi_est: float,
    today_kdj_j_est: float,
) -> Tuple[List[bool], List[str]]:
    """Core signal evaluation using pre-fetched data (shared by realtime and fallback paths)."""
    signals = [False] * 5
    details = []

    # ── Signal ①: MACD红柱连续缩量 ──
    hist_bars = [2.0 * (dif - dea) for dif, dea in zip(macd_difs, macd_deas)]
    yesterday_bar = hist_bars[-1] if len(hist_bars) >= 1 else 0
    day_before_bar = hist_bars[-2] if len(hist_bars) >= 2 else 0
    day3_bar = hist_bars[-3] if len(hist_bars) >= 3 else 0

    if today_bar_est > 0 and yesterday_bar > 0:
        # Check bar shrinking: today < yesterday < day_before
        bar_shrinking = (
            today_bar_est < yesterday_bar and
            (yesterday_bar < day_before_bar if day_before_bar > 0 else True)
        )
        # Alternative: two consecutive shrinking days
        shrinking_2day = (
            today_bar_est < yesterday_bar < day_before_bar
            if day_before_bar > 0 else False
        )
        # Price rising over the same period
        if len(closes_hist) >= 4:
            price_rising = current_price > closes_hist[-4]
        else:
            price_rising = current_price > closes_hist[-1]

        if (bar_shrinking or shrinking_2day) and price_rising:
            signals[0] = True
            details.append(
                f"MACD红柱缩量(est:{today_bar_est:.4f}<昨:{yesterday_bar:.4f}"
                f"<前:{day_before_bar:.4f})"
            )

    # ── Signal ②: RSI顶背离 ──
    if len(closes_hist) >= 20 and len(rsi6s) >= 20:
        # Use last 19 historical + 1 today for extended arrays
        extended_closes = closes_hist[-19:] + [current_price]
        extended_rsi = rsi6s[-19:] + [today_rsi_est]

        peak_idx = extended_closes.index(max(extended_closes))
        peak_close = extended_closes[peak_idx]
        peak_rsi = extended_rsi[peak_idx]
        cur_rsi = extended_rsi[-1]

        if peak_idx < len(extended_closes) - 1:  # peak is not today
            if current_price >= peak_close * 0.99 and cur_rsi < peak_rsi * 0.95:
                signals[1] = True
                details.append(
                    f"RSI顶背离(价{current_price:.2f}≈前高{peak_close:.2f}, "
                    f"RSI{cur_rsi:.1f}<前高RSI{peak_rsi:.1f})"
                )

    # ── Signal ③: 量价背离 ──
    if len(volumes) >= 10 and len(closes_hist) >= 6:
        vol_5d = sum(volumes[-5:]) / 5
        vol_prev_5d = sum(volumes[-10:-5]) / 5
        if vol_prev_5d > 0:
            price_up = current_price > closes_hist[-6]
            vol_decline = vol_5d < vol_prev_5d * 0.8
            if price_up and vol_decline:
                signals[2] = True
                details.append(
                    f"量价背离(近5日均量{vol_5d/1e6:.1f}M"
                    f"<前5日均量{vol_prev_5d/1e6:.1f}M×0.8)"
                )

    # ── Signal ④: KDJ J > 100 ──
    if today_kdj_j_est > 100:
        tag = "实时估算" if today_kdj_j_est != (3.0 * 50.0 - 2.0 * 50.0) else "日频确认"
        signals[3] = True
        details.append(f"KDJ J={today_kdj_j_est:.1f}>100({tag})")

    # ── Signal ⑤: 布林上轨外 ──
    if len(boll_uppers) >= 1 and boll_uppers[-1] > 0:
        if current_price > boll_uppers[-1]:
            signals[4] = True
            details.append(
                f"布林上轨外(现价{current_price:.2f}>上轨{boll_uppers[-1]:.2f})"
            )

    return signals, details


def get_overbought_indicators(
    symbol: str,
    cache: Optional[Dict] = None,
) -> Tuple[float, float, float]:
    """
    Get current KDJ_K, RSI6, and daily change percentage for overbought take-profit.

    Data sources (same pipeline as check_tech_divergence_signals):
      1. Tencent qt.gtimg.cn -> today's live price and change_pct
      2. Tushare stk_factor_pro -> yesterday's confirmed KDJ_K, RSI6
      3. core.realtime_indicators -> intraday KDJ/RSI estimates

    Returns:
        (kdj_k, rsi6, daily_change_pct)
        kdj_k: KDJ K value (0-100), intraday estimate preferred
        rsi6: RSI6 value (0-100), intraday estimate preferred
        daily_change_pct: today's price change percentage (e.g. 3.5 = +3.5%)

    Cache: 60-second TTL, independent of the 1-hour 5-signal cache.
    """
    today_str = datetime.now().strftime('%Y%m%d')
    cache_key = f"ob_{symbol}_{today_str}"
    now = time.time()

    if cache is not None and cache_key in cache:
        cached_result, cached_ts = cache[cache_key]
        if now - cached_ts < 60:
            return cached_result

    default = (50.0, 50.0, 0.0)

    try:
        from app.api.indicator import _normalize_to_ts_code
        from app.config import get_settings

        settings = get_settings()
        token = settings.get_tushare_token()
        if not token:
            return default

        from app.core.trading._api_config import get_tushare_pro as _get_ts_pro
        pro = _get_ts_pro()
        ts_code = _normalize_to_ts_code(symbol)
        end_d = datetime.now().strftime("%Y%m%d")
        start_d = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

        # Fetch yesterday's confirmed indicators
        df_tech = pro.stk_factor_pro(
            ts_code=ts_code, start_date=start_d, end_date=end_d,
            fields='trade_date,close,kdj_k_qfq,kdj_d_qfq,rsi_qfq_6'
        )
        if df_tech is None or df_tech.empty:
            return default
        df_tech = df_tech.sort_values("trade_date", ascending=True)

        latest = df_tech.iloc[-1]
        prev_close = float(latest.get("close", 0) or 0)
        confirmed_kdj_k = float(latest.get("kdj_k_qfq", 0) or 0) or 50.0
        confirmed_rsi6 = float(latest.get("rsi_qfq_6", 0) or 0) or 50.0

        # Fetch daily bars for daily_change_pct fallback
        df_daily = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=5)
        daily_change_pct = 0.0
        if df_daily is not None and len(df_daily) >= 2:
            df_daily = df_daily.sort_values("trade_date", ascending=True)
            yesterday_close = float(df_daily.iloc[-2].get("close", 0) or 0)
            if yesterday_close > 0 and prev_close > 0:
                daily_change_pct = round((prev_close - yesterday_close) / yesterday_close * 100, 2)

        # Try realtime path
        realtime_quote = _fetch_realtime_quote(symbol)
        if realtime_quote and realtime_quote.get('current'):
            try:
                from core.realtime_indicators import (
                    PrevIndicators, DailyBar, calculate_realtime_indicators,
                )

                prev_kdj_d = float(latest.get("kdj_d_qfq", 0) or 0) or 50.0

                prev_indicators = PrevIndicators(
                    trade_date=str(latest["trade_date"]),
                    kdj_k=confirmed_kdj_k,
                    kdj_d=prev_kdj_d,
                    macd_dea=0.0,
                    macd_ema12=prev_close if prev_close > 0 else 0.0,
                    macd_ema26=prev_close if prev_close > 0 else 0.0,
                )

                bars = [
                    DailyBar(
                        trade_date=str(r["trade_date"]),
                        open=float(r["open"]),
                        high=float(r["high"]),
                        low=float(r["low"]),
                        close=float(r["close"]),
                        vol=float(r.get("vol", 0) or 0),
                    )
                    for _, r in df_daily.iterrows()
                ] if df_daily is not None else []

                realtime = calculate_realtime_indicators(
                    symbol="",
                    realtime_quote=realtime_quote,
                    historical_bars=bars,
                    prev_indicators=prev_indicators,
                )

                kdj_k = realtime.kdj_k if realtime.kdj_k else confirmed_kdj_k
                rsi6 = realtime.rsi_6 if realtime.rsi_6 else confirmed_rsi6
                daily_change_pct = float(realtime_quote.get('change_pct', daily_change_pct) or daily_change_pct)

                result = (round(float(kdj_k), 2), round(float(rsi6), 2), round(float(daily_change_pct), 2))
                if cache is not None:
                    cache[cache_key] = (result, now)
                return result

            except Exception:
                pass

        # Fallback: use confirmed values
        result = (round(confirmed_kdj_k, 2), round(confirmed_rsi6, 2), round(daily_change_pct, 2))

    except Exception as e:
        logger.warning(f"[Overbought] get_overbought_indicators failed {symbol}: {e}")
        result = default

    if cache is not None:
        cache[cache_key] = (result, now)

    return result
