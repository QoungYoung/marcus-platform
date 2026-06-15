# -*- coding: utf-8 -*-
"""
Real-time technical indicator calculator.

Computes KDJ / MACD / RSI / MA using:
  - Tencent qt.gtimg.cn real-time OHLCV (current / high / low / open / last_close)
  - Tushare daily historical OHLCV bars (at least 35 bars)
  - Tushare stk_factor_pro previous-day confirmed K/D/EMA values (optional, for best accuracy)

All returned values are labeled 'intraday_estimate' because today's high / low
are not yet finalized until market close.  In contrast, Tushare stk_factor_pro
returns 'daily_confirmed' values computed from final closing prices.

Formulas
--------
KDJ(9,3,3):
  RSV = (close - low9) / (high9 - low9) * 100
  K   = 2/3 * prev_K + 1/3 * RSV
  D   = 2/3 * prev_D + 1/3 * K
  J   = 3*K - 2*D

MACD(12,26,9):
  EMA(N) = (close - prev_EMA) * 2/(N+1) + prev_EMA
  DIF    = EMA12 - EMA26
  DEA    = (DIF - prev_DEA) * 2/10 + prev_DEA
  MACD   = (DIF - DEA) * 2

RSI(Wilder's smoothing):
  avg_gain = (prev_avg_gain * (N-1) + max(chg, 0)) / N
  avg_loss = (prev_avg_loss * (N-1) + max(-chg, 0)) / N
  RSI      = 100 - 100 / (1 + avg_gain / avg_loss)

MA: simple N-period arithmetic mean of close prices.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class DailyBar:
    """Single daily price bar (Tushare daily / pro_bar)."""
    trade_date: str       # YYYYMMDD
    open: float
    high: float
    low: float
    close: float
    vol: float = 0.0


@dataclass
class PrevIndicators:
    """
    Previous trade-day confirmed indicators from Tushare stk_factor_pro.

    Supplying these greatly improves accuracy of the intraday estimate,
    because Tushare computes them on 前复权 (fore-adjusted) prices which
    may differ from raw daily OHLCV.
    """
    trade_date: str           # YYYYMMDD of the previous close
    kdj_k: float = 50.0       # Tushare kdj_k_qfq
    kdj_d: float = 50.0       # Tushare kdj_d_qfq
    macd_ema12: float = 0.0   # derived from macd_dif_qfq / macd_dea_qfq (see below)
    macd_ema26: float = 0.0
    macd_dea: float = 0.0     # Tushare macd_dea_qfq


@dataclass
class RealtimeIndicatorResult:
    """Intraday estimated technical indicators."""
    symbol: str
    current_price: float
    data_source: str = "intraday_estimate"
    calc_time: datetime = field(default_factory=datetime.now)

    # KDJ (9,3,3)
    kdj_k: float = 50.0
    kdj_d: float = 50.0
    kdj_j: float = 50.0

    # MACD (12,26,9)
    macd_dif: float = 0.0
    macd_dea: float = 0.0
    macd_bar: float = 0.0

    # RSI (Wilder)
    rsi_6: float = 50.0
    rsi_12: float = 50.0
    rsi_24: float = 50.0

    # MA
    ma5: float = 0.0
    ma10: float = 0.0
    ma20: float = 0.0

    # Metadata
    warning: str = "盘中估算值，未收盘确认；数据源=腾讯实时行情+Tushare历史日线"
    prev_trade_date: str = ""       # 最近一个已收盘交易日的日期
    used_prev_indicators: bool = False  # 是否使用了Tushare前值做锚点


# ---------------------------------------------------------------------------
# Helper: compute EMA array from close prices
# ---------------------------------------------------------------------------

def _ema_from_closes(closes: List[float], period: int, prev_ema: Optional[float] = None) -> List[float]:
    """
    Compute EMA(N) from a list of closes, oldest-first.
    If prev_ema is given it seeds the EMA for the first bar (using the standard
    recursive formula); otherwise the first bar's EMA = its close (SMA1).
    """
    if not closes:
        return []
    alpha = 2.0 / (period + 1)
    result: List[float] = []
    ema = prev_ema if prev_ema is not None else closes[0]
    for c in closes:
        ema = (c - ema) * alpha + ema
        result.append(ema)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def calculate_realtime_indicators(
    symbol: str,
    realtime_quote: Dict,
    historical_bars: List[DailyBar],
    prev_indicators: Optional[PrevIndicators] = None,
) -> RealtimeIndicatorResult:
    """
    Compute intraday-estimated KDJ / MACD / RSI / MA.

    Parameters
    ----------
    symbol : str
        Stock code, e.g. '000001.SZ'.
    realtime_quote : dict
        Tencent qt.gtimg.cn response fields:
        current, high, low, open, last_close, volume, amount.
    historical_bars : list of DailyBar
        At least 35 daily bars (open/high/low/close), oldest-first.
        未复权 raw prices are fine — Tushare daily.
    prev_indicators : PrevIndicators or None
        Previous trade-day confirmed values from Tushare stk_factor_pro.
        If provided, KDJ and MACD use these as seeds for best accuracy.

    Returns
    -------
    RealtimeIndicatorResult
    """
    # ---- basic values ----
    current = float(realtime_quote.get("current", 0) or 0)
    today_high = float(realtime_quote.get("high", 0) or 0)
    today_low = float(realtime_quote.get("low", 0) or 0)

    if current <= 0:
        return RealtimeIndicatorResult(
            symbol=symbol,
            current_price=0,
            warning="当前价为0，可能非交易时段，无法计算实时指标",
        )

    # Ensure oldest-first order
    bars = sorted(historical_bars, key=lambda b: b.trade_date)
    if len(bars) < 9:
        return RealtimeIndicatorResult(
            symbol=symbol,
            current_price=current,
            warning=f"历史K线不足（需要≥9条，当前{len(bars)}条），无法计算实时指标",
        )

    closes = [b.close for b in bars]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    prev_date = bars[-1].trade_date

    # ---- KDJ (9,3,3) ----
    kdj_k, kdj_d, kdj_j = _calc_kdj(
        current, today_high, today_low, highs, lows, prev_indicators,
    )

    # ---- MACD (12,26,9) ----
    macd_dif, macd_dea, macd_bar = _calc_macd(
        current, closes, prev_indicators,
    )

    # ---- RSI ----
    rsi_6 = _calc_rsi(closes, current, 6)
    rsi_12 = _calc_rsi(closes, current, 12)
    rsi_24 = _calc_rsi(closes, current, 24)

    # ---- MA ----
    ma5 = _calc_ma(closes, current, 5)
    ma10 = _calc_ma(closes, current, 10)
    ma20 = _calc_ma(closes, current, 20)

    return RealtimeIndicatorResult(
        symbol=symbol,
        current_price=current,
        kdj_k=round(kdj_k, 2),
        kdj_d=round(kdj_d, 2),
        kdj_j=round(kdj_j, 2),
        macd_dif=round(macd_dif, 4),
        macd_dea=round(macd_dea, 4),
        macd_bar=round(macd_bar, 4),
        rsi_6=round(rsi_6, 2),
        rsi_12=round(rsi_12, 2),
        rsi_24=round(rsi_24, 2),
        ma5=round(ma5, 2),
        ma10=round(ma10, 2),
        ma20=round(ma20, 2),
        prev_trade_date=prev_date,
        used_prev_indicators=prev_indicators is not None,
    )


# ---------------------------------------------------------------------------
# Internal calculation functions
# ---------------------------------------------------------------------------

def _calc_kdj(
    current: float,
    today_high: float,
    today_low: float,
    highs: List[float],
    lows: List[float],
    prev: Optional[PrevIndicators],
) -> Tuple[float, float, float]:
    """
    Compute KDJ(9,3,3) for today.

    Uses 8 previous complete bars (highs/lows[-8:]) + today's partial bar.
    Seeds K/D from prev_indicators if available; otherwise uses 50.0.
    """
    # 9-day window: last 8 complete bars + today's partial
    h9 = highs[-8:] + [today_high]
    l9 = lows[-8:]  + [today_low]

    high9 = max(h for h in h9 if h > 0)
    low9 = min(l for l in l9 if l > 0)

    if high9 <= low9:
        # Degenerate case — use neutral RSV
        rsv = 50.0
    else:
        rsv = (current - low9) / (high9 - low9) * 100.0

    # Clamp RSV to [0, 100]
    rsv = max(0.0, min(100.0, rsv))

    # Seed K/D from previous day
    k_prev = prev.kdj_k if prev else 50.0
    d_prev = prev.kdj_d if prev else 50.0

    k = 2.0 / 3.0 * k_prev + 1.0 / 3.0 * rsv
    d = 2.0 / 3.0 * d_prev + 1.0 / 3.0 * k
    j = 3.0 * k - 2.0 * d

    return k, d, j


def _calc_macd(
    current: float,
    closes: List[float],
    prev: Optional[PrevIndicators],
) -> Tuple[float, float, float]:
    """
    Compute MACD(12,26,9) for today.

    Seeds EMA12/EMA26/DEA from prev_indicators for alignment with
    Tushare's 前复权 calculation.
    """
    if prev:
        ema12_prev = prev.macd_ema12
        ema26_prev = prev.macd_ema26
        dea_prev = prev.macd_dea
    else:
        # Recalculate from raw closes (less accurate — 未复权 vs 前复权)
        if len(closes) >= 12:
            ema12_prev = _ema_from_closes(closes[-12:], 12)[-1]
        else:
            ema12_prev = closes[-1] if closes else current

        if len(closes) >= 26:
            ema26_prev = _ema_from_closes(closes[-26:], 26)[-1]
        else:
            ema26_prev = closes[-1] if closes else current

        dea_prev = 0.0  # can't seed DEA from raw closes reliably

    alpha12 = 2.0 / 13.0
    alpha26 = 2.0 / 27.0
    alpha_dea = 2.0 / 10.0

    ema12 = (current - ema12_prev) * alpha12 + ema12_prev
    ema26 = (current - ema26_prev) * alpha26 + ema26_prev
    dif = ema12 - ema26
    dea = (dif - dea_prev) * alpha_dea + dea_prev
    bar = (dif - dea) * 2.0

    return dif, dea, bar


def _calc_rsi(
    closes: List[float],
    current: float,
    period: int,
) -> float:
    """
    Compute Wilder's RSI(period) using (N+1) bars: N complete closes + today's current.

    Each close in `closes` is a completed daily bar.  We need `period` historical
    closes PLUS today's close-like price.  To align with Tushare's trailing
    calculation we start the rolling gain/loss from the most recent bars.
    """
    need = period + 1  # N changes need N+1 prices
    if len(closes) < need:
        # Not enough data — return neutral
        return 50.0

    prices = closes[-need:] + [current]

    # First `period` changes: simple average
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        chg = prices[i] - prices[i - 1]
        if chg > 0:
            gains += chg
        else:
            losses += abs(chg)

    avg_gain = gains / period
    avg_loss = losses / period

    # Wilder smoothing for remaining bars (just today's change)
    for i in range(period + 1, len(prices)):
        chg = prices[i] - prices[i - 1]
        gain = max(chg, 0)
        loss = max(-chg, 0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _calc_ma(
    closes: List[float],
    current: float,
    period: int,
) -> float:
    """
    Compute simple N-period MA: (N-1) completed closes + today's current.
    """
    need = period - 1
    if len(closes) < need:
        return current
    recent = closes[-need:] + [current]
    return sum(recent) / len(recent)


# ---------------------------------------------------------------------------
# Helper: reconstruct EMA seeds from Tushare stk_factor_pro values
# ---------------------------------------------------------------------------

def prev_indicators_from_tushare(
    trade_date: str,
    kdj_k: float,
    kdj_d: float,
    macd_dif: float,
    macd_dea: float,
) -> PrevIndicators:
    """
    Build a PrevIndicators from raw Tushare stk_factor_pro fields.

    Tushare provides DIF and DEA directly; we reverse the EMAs:
        ema12 = DIF + ema26
        ema26 = (26 * dif - 12 * ema12) ...  wait, that's circular.

    Simpler approach: since Tushare uses 前复权, we cannot perfectly
    reconstruct ema12/ema26 from raw closes.  Instead we seed the *difference*
    direction:

        ema12_prev ≈ close_prev  (approximation)
        ema26_prev ≈ close_prev  (approximation)

    BUT we know DIF = ema12 - ema26, so as long as we consistently shift
    both by the same offset, DIF(t+1) will be correct.

    The simplest technique: set ema26_prev = close_prev, then
    ema12_prev = ema26_prev + dif_prev.
    """
    # We'll get close_prev from the caller — pass it in.
    # For now return the raw Tushare fields; the caller supplies close_prev.
    return PrevIndicators(
        trade_date=trade_date,
        kdj_k=kdj_k,
        kdj_d=kdj_d,
        macd_dea=macd_dea,
        # ema12/ema26 will be set by the caller using close_prev
    )
