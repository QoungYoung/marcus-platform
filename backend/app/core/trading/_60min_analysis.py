#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
60分钟K线级别分析 — 用于止损监控 v2.6。

信号判定：
  60min bullish  (5条件全满足): close>ma5, ma5>ma10, dif>0, bar>0, close>ma20
  60min weakening(5中任意2):     close<ma5, ma5↓, bar缩2期, dif↓, close<ma10
  60min bearish  (4条件全满足): close<ma5, ma5<ma10, dif<0, bar<0

日线背离检测（3中任意2）：
  ① MACD红柱缩量  ② RSI顶背离  ③ 量价背离

决策矩阵：
  60min\日线    | 日线健康(<2背离)  | 日线背离(≥2)
  ─────────────┼──────────────────┼──────────────
  bullish      | 持有/不加仓       | 减仓 1/3~1/2
  weakening    | 观望(持有)        | 减仓 2/3
  bearish      | 减仓 1/2         | 清仓

数据源：
  1. Tushare rt_min_daily(freq="60min") — 实时60分钟K线（tu.brze.top 代理）
  2. 日线技术指标近似（60min MA10≈日线MA5, 60min MA20≈日线MA10）
"""

import json
import logging
import time
import urllib.request
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict

logger = logging.getLogger(__name__)

# tu.brze.top 代理配置（仅用于 rt_min_daily）
_RT_MIN_TOKEN = 'SC9b-_EoiR-gUuR1hHMIddmTqHvF6D_DGOizKGo2KQk'
_RT_MIN_URL = 'https://tu.brze.top/api'


def _call_rt_min_daily_raw(ts_codes: list, freq: str) -> Optional[dict]:
    """直接 HTTP 调用 tu.brze.top rt_min_daily，绕过 Tushare SDK（SDK 无法解析此代理的响应格式）。

    返回 {"fields": [...], "items": [[...], ...]} 或 None。
    """
    time.sleep(1.0)
    payload = json.dumps({
        'api_name': 'rt_min_daily',
        'token': _RT_MIN_TOKEN,
        'params': {'ts_code': ','.join(ts_codes), 'freq': freq},
        'fields': '',
    }).encode('utf-8')
    req = urllib.request.Request(
        _RT_MIN_URL, data=payload,
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.warning(f"[rt_min_daily] HTTP 请求失败: {e}")
        return None
    if data.get('code') != 0 or not data.get('data'):
        return None
    return data['data']

# 缓存：避免同一扫描周期重复 Tushare 调用
_signal_cache: Dict[str, Tuple[float, tuple]] = {}  # key -> (timestamp, result)
_cache_ttl: float = 120.0  # 2分钟


def _normalize_to_ts_code(symbol: str) -> str:
    """标准化股票代码为 Tushare 格式"""
    from app.api.indicator import _normalize_to_ts_code as _norm
    return _norm(symbol)


def _fetch_60min_bars_today(ts_code: str) -> Optional[List[dict]]:
    """从 rt_min_daily 获取今日60分钟K线（tu.brze.top 代理）"""
    try:
        data = _call_rt_min_daily_raw([ts_code], "60min")
        if not data:
            return None
        fields = data.get("fields", [])
        items = data.get("items", [])
        if not fields or not items:
            return None
        col_map = {name: idx for idx, name in enumerate(fields)}
        bars = []
        for row in items:
            bars.append({
                "time": str(row[col_map.get("trade_time", 1)]),
                "open": float(row[col_map.get("open", 2)]),
                "close": float(row[col_map.get("close", 3)]),
                "high": float(row[col_map.get("high", 4)]),
                "low": float(row[col_map.get("low", 5)]),
                "vol": float(row[col_map.get("vol", 6)]),
            })
        bars.sort(key=lambda b: b["time"])
        return bars
    except Exception as e:
        logger.debug(f"[60min] rt_min_daily 获取失败 {ts_code}: {e}")
        return None


def _fetch_60min_bars_history(ts_code: str, days: int = 20) -> Optional[List[dict]]:
    """从 stk_mins 获取历史60分钟K线（主代理，用于计算 MA10/MA30 等）"""
    try:
        from app.core.trading._api_config import get_tushare_pro
        pro = get_tushare_pro()
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=days)
        df = pro.stk_mins(
            ts_code=ts_code, freq='60min',
            start_date=start_dt.strftime('%Y-%m-%d 09:30:00'),
            end_date=end_dt.strftime('%Y-%m-%d 15:00:00'),
        )
        if df is None or df.empty:
            return None
        df = df.sort_values('trade_time', ascending=True)
        bars = []
        for _, row in df.iterrows():
            bars.append({
                "time": str(row.get("trade_time", "")),
                "open": float(row.get("open", 0)),
                "close": float(row.get("close", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "vol": float(row.get("vol", 0)),
            })
        return bars
    except Exception as e:
        logger.debug(f"[60min] stk_mins 获取失败 {ts_code}: {e}")
        return None


def _fetch_60min_bars_merged(ts_code: str) -> Optional[List[dict]]:
    """获取历史+今日60分钟K线，合并去重（今日 rt_min_daily 覆盖历史同时间）"""
    hist_bars = _fetch_60min_bars_history(ts_code)
    today_bars = _fetch_60min_bars_today(ts_code)

    if not hist_bars and not today_bars:
        return None

    merged = {}
    if hist_bars:
        for b in hist_bars:
            merged[b["time"]] = b
    if today_bars:
        for b in today_bars:
            merged[b["time"]] = b  # 当日数据覆盖历史同时间

    result = sorted(merged.values(), key=lambda b: b["time"])
    return result if result else None


def _sma(values: List[float], period: int) -> List[float]:
    """简单移动平均"""
    if len(values) < period:
        return [sum(values) / len(values)] * len(values) if values else []
    result = []
    for i in range(len(values)):
        if i < period - 1:
            window = values[:i + 1]
            result.append(sum(window) / len(window))
        else:
            result.append(sum(values[i - period + 1:i + 1]) / period)
    return result


def _ema(values: List[float], period: int) -> List[float]:
    """指数移动平均"""
    if not values:
        return []
    k = 2.0 / (period + 1)
    result = [values[0]]
    for v in values[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _calc_macd(closes: List[float]) -> dict:
    """从收盘价序列计算 MACD"""
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    dea = _ema(dif, 9)
    bar = [2.0 * (d - e) for d, e in zip(dif, dea)]
    return {
        "dif": dif,
        "dea": dea,
        "bar": bar,
        "dif_latest": dif[-1] if dif else 0,
        "dea_latest": dea[-1] if dea else 0,
        "bar_latest": bar[-1] if bar else 0,
    }


def calc_60min_indicators_from_bars(bars: List[dict]) -> Optional[dict]:
    """从60分钟K线计算技术指标（需≥5根，MA10/MA20/MA30 不足时按窗口宽度降级）"""
    if not bars or len(bars) < 5:
        return None

    closes = [b["close"] for b in bars]
    n = len(closes)
    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10) if n >= 10 else _sma(closes, n)
    ma20 = _sma(closes, 20) if n >= 20 else _sma(closes, min(n, 19))
    ma30 = _sma(closes, 30) if n >= 30 else _sma(closes, min(n, 29))
    macd = _calc_macd(closes)

    return {
        "close": closes[-1],
        "ma5": ma5[-1] if ma5 else 0,
        "ma10": ma10[-1] if ma10 else 0,
        "ma20": ma20[-1] if ma20 else 0,
        "ma30": ma30[-1] if ma30 else 0,
        "ma5_prev": ma5[-2] if len(ma5) >= 2 else ma5[-1] if ma5 else 0,
        "dif": macd["dif_latest"],
        "dea": macd["dea_latest"],
        "bar": macd["bar_latest"],
        "bar_prev": macd["bar"][-2] if len(macd["bar"]) >= 2 else 0,
        "bar_prev2": macd["bar"][-3] if len(macd["bar"]) >= 3 else 0,
        "dif_prev": macd["dif"][-2] if len(macd["dif"]) >= 2 else macd["dif_latest"],
        "bar_count": len(bars),
        "source": "rt_min_60min",
    }


def calc_60min_indicators_from_daily(symbol: str, current_price: float) -> Optional[dict]:
    """用日线技术指标近似60分钟级别指标。

    近似映射：60min MA10 ≈ 日线 MA5, 60min MA20 ≈ 日线 MA10
    60min MACD ≈ 日线 MACD（基于日线 DIF/DEA/bar）
    """
    try:
        from app.api.indicator import _normalize_to_ts_code
        from app.config import get_settings
        from app.core.trading._api_config import get_tushare_pro as _get_ts_pro
        from datetime import datetime as dt

        settings = get_settings()
        token = settings.get_tushare_token()
        if not token:
            return None

        pro = _get_ts_pro()
        ts_code = _normalize_to_ts_code(symbol)
        end_d = dt.now().strftime("%Y%m%d")
        start_d = (dt.now() - timedelta(days=60)).strftime("%Y%m%d")

        # 日线数据：用于计算日线 MAs
        df_daily = pro.daily(ts_code=ts_code, start_date=start_d, end_date=end_d, limit=30)
        if df_daily is None or df_daily.empty or len(df_daily) < 5:
            return None
        df_daily = df_daily.sort_values("trade_date", ascending=True)
        closes_daily = [float(v) for v in df_daily["close"].values]

        # 日线技术指标：MACD/RSI 等
        df_tech = pro.stk_factor_pro(
            ts_code=ts_code, start_date=start_d, end_date=end_d,
            fields='trade_date,close,macd_dif_qfq,macd_dea_qfq,rsi_qfq_6'
        )
        if df_tech is None or df_tech.empty:
            return None
        df_tech = df_tech.sort_values("trade_date", ascending=True)

        # ── 日线 MAs ──
        ma5_daily = _sma(closes_daily, 5)
        ma10_daily = _sma(closes_daily, 10)
        ma20_daily = _sma(closes_daily, 20)

        # ── 映射到60分钟级别 ──
        # 60min MA5  ≈ 最近1.25天，用日线 MA2 或直接用 current_price 附近值
        # 60min MA10 ≈ 日线 MA5（约2.5天）
        # 60min MA20 ≈ 日线 MA10（约5天）
        ma5_60m = ma5_daily[-2] if len(ma5_daily) >= 2 else ma5_daily[-1]  # 用前一日MA5≈60min MA10
        ma10_60m = ma10_daily[-1] if ma10_daily else 0  # 日线 MA10 ≈ 60min MA20
        ma20_60m = ma20_daily[-1] if ma20_daily else 0  # 日线 MA20 ≈ 60min MA30

        # 60min MACD ≈ 日线 MACD
        dif_latest = float(df_tech["macd_dif_qfq"].values[-1])
        dea_latest = float(df_tech["macd_dea_qfq"].values[-1])
        bar_latest = 2.0 * (dif_latest - dea_latest)

        dif_prev = float(df_tech["macd_dif_qfq"].values[-2]) if len(df_tech) >= 2 else dif_latest
        dea_prev = float(df_tech["macd_dea_qfq"].values[-2]) if len(df_tech) >= 2 else dea_latest
        bar_prev = 2.0 * (dif_prev - dea_prev)

        dea_prev2 = float(df_tech["macd_dea_qfq"].values[-3]) if len(df_tech) >= 3 else dea_prev
        dif_prev2 = float(df_tech["macd_dif_qfq"].values[-3]) if len(df_tech) >= 3 else dif_prev
        bar_prev2 = 2.0 * (dif_prev2 - dea_prev2)

        return {
            "close": current_price,
            "ma5": ma5_60m,
            "ma10": ma10_60m,
            "ma20": ma20_60m,
            "ma30": ma20_daily[-1] if ma20_daily else 0,
            "ma5_prev": ma5_daily[-2] if len(ma5_daily) >= 2 else ma5_60m,
            "dif": dif_latest,
            "dea": dea_latest,
            "bar": bar_latest,
            "bar_prev": bar_prev,
            "bar_prev2": bar_prev2,
            "dif_prev": dif_prev,
            "bar_count": 0,
            "source": "daily_approx",
        }
    except Exception as e:
        logger.warning(f"[60min] 日线近似计算失败 {symbol}: {e}")
        return None


def get_60min_indicators(symbol: str, current_price: float) -> Optional[dict]:
    """获取60分钟级别技术指标。

    数据优先级：
    1. stk_mins（历史，~20天）+ rt_min_daily（当日）合并 → 真实 60min MA/MA10/MA20/MA30
    2. 降级：日线近似（当日线指标无法获取足够分钟K线时）
    """
    try:
        from app.api.indicator import _normalize_to_ts_code
        ts_code = _normalize_to_ts_code(symbol)

        # 优先：实际60分钟K线（历史+当日合并）
        bars = _fetch_60min_bars_merged(ts_code)
        if bars and len(bars) >= 5:
            result = calc_60min_indicators_from_bars(bars)
            if result:
                return result

        # 降级：日线近似
        return calc_60min_indicators_from_daily(symbol, current_price)
    except Exception as e:
        logger.warning(f"[60min] 指标获取失败 {symbol}: {e}")
        return None


# ── 信号判定 ──

def is_60min_bullish(ind: dict) -> Tuple[bool, str]:
    """60分钟看涨信号：5个条件全部满足。

    ① close > ma5
    ② ma5 > ma10
    ③ dif > 0
    ④ bar > 0 (DIF > DEA)
    ⑤ close > ma20
    """
    conditions = []
    ok = []

    c1 = ind["close"] > ind["ma5"] > 0
    conditions.append(("close>MA5", c1))
    if c1:
        ok.append(1)

    c2 = ind["ma5"] > ind["ma10"] > 0
    conditions.append(("MA5>MA10", c2))
    if c2:
        ok.append(2)

    c3 = ind["dif"] > 0
    conditions.append(("DIF>0", c3))
    if c3:
        ok.append(3)

    c4 = ind["bar"] > 0
    conditions.append(("bar>0", c4))
    if c4:
        ok.append(4)

    c5 = ind["ma20"] > 0 and ind["close"] > ind["ma20"]
    conditions.append(("close>MA20", c5))
    if c5:
        ok.append(5)

    is_bull = len(ok) == 5
    detail = f"bullish({len(ok)}/5:{','.join(str(x) for x in ok)})" if ok else "bullish(0/5)"
    return is_bull, detail


def is_60min_weakening(ind: dict) -> Tuple[bool, int, str]:
    """60分钟走弱信号：5个条件中任意2个满足。

    ① close < ma5
    ② ma5 下降
    ③ bar 连续2期缩量
    ④ dif 下降
    ⑤ close < ma10
    """
    count = 0
    triggered = []

    if ind["close"] < ind["ma5"]:
        count += 1
        triggered.append("close<MA5")

    if ind["ma5"] < ind["ma5_prev"]:
        count += 1
        triggered.append("MA5↓")

    if ind["bar"] < ind["bar_prev"] < ind["bar_prev2"] and ind["bar_prev"] != 0:
        count += 1
        triggered.append("bar缩2期")

    if ind["dif"] < ind["dif_prev"]:
        count += 1
        triggered.append("DIF↓")

    if ind["ma10"] > 0 and ind["close"] < ind["ma10"]:
        count += 1
        triggered.append("close<MA10")

    detail = f"weakening({count}/5:{','.join(triggered)})" if triggered else "weakening(0/5)"
    return count >= 2, count, detail


def is_60min_bearish(ind: dict) -> Tuple[bool, str]:
    """60分钟看跌信号：4个条件全部满足。

    ① close < ma5
    ② ma5 < ma10
    ③ dif < 0
    ④ bar < 0 (DIF < DEA)
    """
    conditions = []
    ok = []

    c1 = ind["close"] < ind["ma5"]
    conditions.append(("close<MA5", c1))
    if c1:
        ok.append(1)

    c2 = ind["ma5"] < ind["ma10"] and ind["ma5"] > 0
    conditions.append(("MA5<MA10", c2))
    if c2:
        ok.append(2)

    c3 = ind["dif"] < 0
    conditions.append(("DIF<0", c3))
    if c3:
        ok.append(3)

    c4 = ind["bar"] < 0
    conditions.append(("bar<0", c4))
    if c4:
        ok.append(4)

    is_bear = len(ok) == 4
    detail = f"bearish({len(ok)}/4:{','.join(str(x) for x in ok)})" if ok else "bearish(0/4)"
    return is_bear, detail


def check_daily_divergence(symbol: str, current_price: float, float_pnl_pct: float) -> Tuple[bool, int, str]:
    """日线背离检测：3个信号中任意2个满足。

    复用 _tech_divergence 的 5 信号检测，取前 3 个：
    ① MACD红柱缩量  ② RSI顶背离  ③ 量价背离
    """
    from app.core.trading._tech_divergence import check_tech_divergence_signals

    signals, details = check_tech_divergence_signals(
        symbol=symbol,
        current_price=current_price,
        float_pnl_pct=float_pnl_pct,
    )

    # 只取前 3 个信号（MACD红柱缩量 / RSI顶背离 / 量价背离）
    div_count = sum(signals[:3])
    div_details = [d for i, d in enumerate(details) if i < 3 and signals[i]]

    detail_str = f"日线背离({div_count}/3:{';'.join(div_details)})" if div_details else f"日线背离({div_count}/3)"
    return div_count >= 2, div_count, detail_str


def evaluate_60min_stop(
    symbol: str,
    current_price: float,
    float_pnl_pct: float,
) -> Tuple[Optional[str], float]:
    """60分钟级别止损评估 — 决策矩阵。

    Returns:
        (reason, sell_ratio)
        - reason=None → 不触发
        - sell_ratio: 0.33=减1/3, 0.5=减半, 0.67=减2/3, 1.0=清仓
    """
    # ── 缓存检查 ──
    cache_key = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M')[:-1]}"
    now_ts = datetime.now().timestamp()
    if cache_key in _signal_cache:
        cached_ts, cached_result = _signal_cache[cache_key]
        if now_ts - cached_ts < _cache_ttl:
            return cached_result

    # ── 获取60分钟指标 ──
    ind = get_60min_indicators(symbol, current_price)
    if ind is None:
        return None, 1.0

    # ── 判定60分钟结构 ──
    bullish, bull_detail = is_60min_bullish(ind)
    weakening, weak_count, weak_detail = is_60min_weakening(ind)
    bearish, bear_detail = is_60min_bearish(ind)

    # ── 判定日线背离 ──
    has_div, div_count, div_detail = check_daily_divergence(symbol, current_price, float_pnl_pct)

    source_tag = ind.get("source", "?")

    # ── 决策矩阵 ──
    reason = None
    sell_ratio = 1.0

    if bullish:
        if has_div:
            # 60min bullish + 日线背离 → 减仓 1/3~1/2
            ratio = 0.4  # 取中间值
            reason = (
                f"60分钟看涨+日线背离减仓: {bull_detail}, {div_detail} "
                f"[{source_tag}] → 减仓{ratio*100:.0f}%"
            )
            sell_ratio = ratio
        else:
            # 60min bullish + 日线健康 → 持有，不触发
            reason = None
            sell_ratio = 1.0
    elif bearish:
        if has_div:
            # 60min bearish + 日线背离 → 清仓
            reason = (
                f"60分钟看跌+日线背离清仓: {bear_detail}, {div_detail} "
                f"[{source_tag}] → 清仓"
            )
            sell_ratio = 1.0
        else:
            # 60min bearish + 日线健康 → 减仓 1/2
            reason = (
                f"60分钟看跌减仓: {bear_detail}, {div_detail} "
                f"[{source_tag}] → 减仓50%"
            )
            sell_ratio = 0.5
    elif weakening:
        if has_div:
            # 60min weakening + 日线背离 → 减仓 2/3
            reason = (
                f"60分钟走弱+日线背离减仓: {weak_detail}, {div_detail} "
                f"[{source_tag}] → 减仓67%"
            )
            sell_ratio = 0.67
        else:
            # 60min weakening + 日线健康 → 观望，不触发
            reason = None
            sell_ratio = 1.0
    else:
        # 无明显60分钟信号 → 不触发
        reason = None
        sell_ratio = 1.0

    # ── 缓存结果 ──
    _signal_cache[cache_key] = (now_ts, (reason, sell_ratio))

    return reason, sell_ratio
