# -*- coding: utf-8 -*-
"""Industry Leaderboard Service — 申万一级行业龙头股实时排行。

Two-round scoring architecture:
  Round 1: ~330 candidates, 4 dimensions (Trend, Volume-Price, Industry Relative, Price Residual)
           via 3 batch API calls (Tencent realtime + Tushare stk_factor_pro + Tushare daily)
  Round 2: Top 10 capital persistence scoring via East Money real-time (serial HTTP)
"""

import json
import logging
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── 缓存 ──
_leaderboard_cache: Dict[str, Tuple[float, dict]] = {}
_CACHE_TTL = 60  # 秒


def _get_data_dir() -> Path:
    """Resolve data directory (stock_pool.db location)."""
    try:
        from app.config import get_settings
        return get_settings().data_dir
    except Exception:
        pass
    candidates = [
        Path(__file__).resolve().parents[3] / "data",
        Path(os.getcwd()) / "data",
    ]
    for c in candidates:
        if c.exists():
            return c
    return Path(os.getcwd()) / "data"


def _get_tushare_pro():
    """Unified Tushare pro_api instance."""
    from app.core.trading._api_config import get_tushare_pro as _gtp
    return _gtp()


# ── 腾讯行情字段索引 ──
# qt.gtimg.cn 返回格式: var hq_str_sh600519="name,code,price,..."
# 以 ~ 分隔，字段索引:
_TX_PRICE = 3       # 当前价
_TX_CHANGE_PCT = 32  # 涨跌幅(%)
_TX_TURNOVER_RATE = 38  # 换手率(%)
_TX_AMOUNT = 37      # 成交额(万元)
_TX_HIGH = 33        # 最高价
_TX_LOW = 34         # 最低价
_TX_OPEN = 5         # 开盘价
_TX_PRE_CLOSE = 4    # 昨收价

# ── 市场状态判别 ──
REGIME_TRENDING = "trending"
REGIME_RANGING = "ranging"
REGIME_TRANSITIONAL = "transitional"

# ── 权重方案 ──
WEIGHTS = {
    REGIME_TRENDING: {
        "trend": 0.28, "volume_price": 0.15,
        "industry_relative": 0.17, "price_residual": 0.15, "capital": 0.25,
    },
    REGIME_RANGING: {
        "trend": 0.22, "volume_price": 0.18,
        "industry_relative": 0.20, "price_residual": 0.18, "capital": 0.22,
    },
    REGIME_TRANSITIONAL: {
        "trend": 0.25, "volume_price": 0.165,
        "industry_relative": 0.185, "price_residual": 0.165, "capital": 0.235,
    },
}


class IndustryLeaderboardService:
    """行业龙头股实时排行服务。"""

    def __init__(self, db_path: str = None):
        self._db_path = db_path or str(_get_data_dir() / "stock_pool.db")

    # ── 主入口 ─────────────────────────────────────────────

    def get_leaderboard(
        self,
        limit: int = 50,
        sort_by: str = "composite_score",
        industry: Optional[str] = None,
        refresh: bool = False,
        date: Optional[str] = None,
    ) -> dict:
        """Main entry: fetch, score, rank, cache.

        Args:
            date: Optional historical date in YYYYMMDD format.
                  When provided, uses Tushare historical data exclusively.
                  When None, uses real-time sources (Tencent, East Money).
        """
        is_historical = bool(date)
        cache_key = f"{sort_by}:{industry or ''}:{date or 'realtime'}"
        now = time.time()

        if not refresh and cache_key in _leaderboard_cache:
            ts, data = _leaderboard_cache[cache_key]
            # Historical dates: permanent cache (no TTL expiry)
            if is_historical or (now - ts < _CACHE_TTL):
                logger.info(f"[Leaderboard] Cache hit for '{cache_key}' ({now - ts:.0f}s ago)")
                return data

        logger.info(f"[Leaderboard] Round 1: fetching ~330 candidates + 3 batch APIs (historical={is_historical})...")

        # 1.2 候选股筛选
        candidates = self._get_industry_candidates()
        if not candidates:
            return {"items": [], "market_regime": REGIME_TRANSITIONAL, "industries_covered": [],
                    "data_source": "tencent", "volume_data": "full",
                    "trading_days": [], "updated_at": datetime.now().isoformat()}

        symbols = [c["ts_code"] for c in candidates]
        logger.info(f"[Leaderboard] {len(symbols)} candidates from {len(set(c['industry'] for c in candidates))} industries")

        # 1.6 市场状态 (历史模式下指定日期)
        regime = self._detect_market_regime(as_of_date=date)
        logger.info(f"[Leaderboard] Market regime: {regime}")

        if is_historical:
            # ── 历史模式：全部使用 Tushare 盘后数据 ──
            quotes = self._historical_quotes(symbols, date)
            data_source = "tushare"
            logger.info(f"[Leaderboard] Historical quotes: {len(quotes)} stocks for {date}")

            indicators = self._historical_indicators(symbols, date)
            logger.info(f"[Leaderboard] Historical indicators: {len(indicators)} stocks for {date}")

            daily_bars, volume_data_status = self._fetch_daily_bars_batch(symbols, end_date=date)
            logger.info(f"[Leaderboard] Historical daily bars: {len(daily_bars)} stocks ({volume_data_status})")
        else:
            # ── 实时模式：现有逻辑不变 ──
            # 1.3 实时行情 (腾讯)
            quotes, data_source = self._fetch_realtime_quotes_batch(symbols)
            logger.info(f"[Leaderboard] Quotes: {len(quotes)} stocks (source={data_source})")

            # 1.4 技术指标 (Tushare stk_factor_pro)
            indicators = self._fetch_indicators_batch(symbols)
            logger.info(f"[Leaderboard] Indicators: {len(indicators)} stocks")

            # 1.5 日线数据 (Tushare daily)
            daily_bars, volume_data_status = self._fetch_daily_bars_batch(symbols)
            logger.info(f"[Leaderboard] Daily bars: {len(daily_bars)} stocks ({volume_data_status})")

        # 1.9 硬过滤
        candidates, excluded = self._apply_hard_filters(candidates, quotes, indicators)
        logger.info(f"[Leaderboard] After hard filters: {len(candidates)} candidates (excluded {len(excluded)})")

        # 1.7 Round 1 四维评分
        w = WEIGHTS[regime] if regime in WEIGHTS else WEIGHTS[REGIME_TRANSITIONAL]
        trend_scores, trend_details = self._compute_trend_composite(candidates, indicators, regime)
        volume_scores, vol_details = self._compute_volume_price(candidates, indicators, daily_bars, regime)
        industry_scores, ind_details = self._compute_industry_relative_strength(candidates, quotes, daily_bars, regime)
        residual_scores, res_details = self._compute_price_residual(candidates, quotes, indicators, regime)

        # 资金维度先取中性分
        capital_max = 25 if regime == REGIME_TRENDING else 22
        capital_neutral = capital_max * 0.5

        # 计算综合分
        for c in candidates:
            sym = c["ts_code"]
            c["trend_score"] = round(trend_scores.get(sym, 0), 1)
            c["volume_price_score"] = round(volume_scores.get(sym, 0), 1)
            c["industry_relative_score"] = round(industry_scores.get(sym, 0), 1)
            c["price_residual_score"] = round(residual_scores.get(sym, 0), 1)
            c["capital_score"] = round(capital_neutral, 1)
            c["capital_data"] = "neutral"
            c["composite_score"] = round(
                c["trend_score"] * w["trend"] * 100 / 28 +
                c["volume_price_score"] * w["volume_price"] * 100 / 15 +
                c["industry_relative_score"] * w["industry_relative"] * 100 / 17 +
                c["price_residual_score"] * w["price_residual"] * 100 / 15 +
                c["capital_score"] * w["capital"] * 100 / 25,
                1
            )

        # 1.10 惩罚
        self._apply_penalties(candidates, indicators)

        # 排序
        candidates.sort(key=lambda x: x.get("composite_score", 0), reverse=True)

        # 1.8 Round 2: Top 10 资金补算
        top10 = candidates[:10]
        if is_historical:
            moneyflows = self._historical_moneyflow([c["ts_code"] for c in top10], date)
        else:
            moneyflows = self._fetch_top10_moneyflow([c["ts_code"] for c in top10])
        capital_scores, cap_details = self._compute_capital_persistence(top10, moneyflows, regime)

        logger.info(f"[Leaderboard] Round2: moneyflows={len(moneyflows)}, capital_scores={len(capital_scores)}, "
                    f"top10_ts_codes={[c['ts_code'] for c in top10]}")

        for c in top10:
            sym = c["ts_code"]
            new_cap = capital_scores.get(sym)
            if new_cap is not None and moneyflows.get(sym):
                c["capital_score"] = round(new_cap, 1)
                c["capital_data"] = "available"
            elif new_cap is not None:
                c["capital_score"] = round(new_cap, 1)
                c["capital_data"] = "unavailable"

        logger.info(f"[Leaderboard] Round2 result: available={sum(1 for c in top10 if c.get('capital_data')=='available')}, "
                    f"unavailable={sum(1 for c in top10 if c.get('capital_data')=='unavailable')}, "
                    f"neutral={sum(1 for c in top10 if c.get('capital_data')=='neutral')}")

        # Top10 重排序
        for c in top10:
            c["composite_score"] = round(
                c["trend_score"] * w["trend"] * 100 / 28 +
                c["volume_price_score"] * w["volume_price"] * 100 / 15 +
                c["industry_relative_score"] * w["industry_relative"] * 100 / 17 +
                c["price_residual_score"] * w["price_residual"] * 100 / 15 +
                c["capital_score"] * w["capital"] * 100 / 25,
                1
            )
        candidates.sort(key=lambda x: x.get("composite_score", 0), reverse=True)

        # 行业筛选
        if industry:
            candidates = [c for c in candidates if c.get("industry") == industry]

        # 排序 & limit
        score_map = {
            "composite_score": "composite_score",
            "trend_score": "trend_score",
            "volume_price_score": "volume_price_score",
            "industry_relative_score": "industry_relative_score",
            "price_residual_score": "price_residual_score",
            "capital_score": "capital_score",
            "change_pct": "change_pct",
        }
        sort_field = score_map.get(sort_by, "composite_score")
        candidates.sort(key=lambda x: x.get(sort_field, 0), reverse=True)
        candidates = candidates[:limit]

        # 1.9 资金补漏：最终 top-N 中不在原始 top10 的股票，补算历史资金流
        if is_historical:
            need_capital = [c for c in candidates if c.get("capital_data", "neutral") == "neutral"]
            if need_capital:
                logger.info(f"[Leaderboard] Capital补漏: {len(need_capital)} stocks need capital data")
                extra_mf = self._historical_moneyflow([c["ts_code"] for c in need_capital], date)
                extra_scores, extra_details = self._compute_capital_persistence(need_capital, extra_mf, regime)
                for c in need_capital:
                    sym = c["ts_code"]
                    new_cap = extra_scores.get(sym)
                    if new_cap is not None and extra_mf.get(sym):
                        c["capital_score"] = round(new_cap, 1)
                        c["capital_data"] = "available"
                        # 汇总到 cap_details 供后续组装使用
                        if sym in extra_details:
                            cap_details[sym] = extra_details[sym]
                    elif new_cap is not None:
                        c["capital_score"] = round(new_cap, 1)
                        c["capital_data"] = "unavailable"
                # 重算综合分并重排
                for c in need_capital:
                    if c.get("capital_data") in ("available", "unavailable"):
                        c["composite_score"] = round(
                            c["trend_score"] * w["trend"] * 100 / 28 +
                            c["volume_price_score"] * w["volume_price"] * 100 / 15 +
                            c["industry_relative_score"] * w["industry_relative"] * 100 / 17 +
                            c["price_residual_score"] * w["price_residual"] * 100 / 15 +
                            c["capital_score"] * w["capital"] * 100 / 25,
                            1
                        )
                candidates.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
                logger.info(f"[Leaderboard] Capital补漏: completed, "
                            f"updated={sum(1 for c in need_capital if c.get('capital_data') in ('available','unavailable'))}/{len(need_capital)}")

        # 构建响应
        items = []
        for c in candidates:
            sym = c["ts_code"]
            q = quotes.get(sym, {})
            cap_data = c.get("capital_data", "neutral")
            cap_score = c.get("capital_score", 0)

            # 组装资金维度明细
            if cap_data == "available" and sym in cap_details:
                cap_detail = cap_details[sym]
            else:
                cap_reason = "非Top10股票，资金分取中性值(max*0.5)，未使用东方财富实时数据" if cap_data == "neutral" else "东方财富接口不可用，资金分取中性值(max*0.5)"
                cap_detail = {
                    "label": "资金持续性",
                    "score": round(cap_score, 1),
                    "max": capital_max,
                    "sub_scores": [
                        {"label": "数据说明", "score": round(cap_score, 1), "max": capital_max, "reason": cap_reason},
                    ],
                }

            # 组装 score_detail
            score_detail = {
                "trend": trend_details.get(sym, {}),
                "volume_price": vol_details.get(sym, {}),
                "industry_relative": ind_details.get(sym, {}),
                "price_residual": res_details.get(sym, {}),
                "capital": cap_detail,
            }

            items.append({
                "symbol": c["ts_code"],
                "name": c["name"],
                "industry": c["industry"],
                "market_cap": c.get("market_cap", 0),
                "change_pct": q.get("change_pct", 0),
                "turnover_rate": q.get("turnover_rate", 0),
                "turnover_amount": q.get("amount", 0),
                "composite_score": c.get("composite_score", 0),
                "trend_score": c.get("trend_score", 0),
                "volume_price_score": c.get("volume_price_score", 0),
                "industry_relative_score": c.get("industry_relative_score", 0),
                "price_residual_score": c.get("price_residual_score", 0),
                "capital_score": cap_score,
                "capital_data": cap_data,
                "warnings": c.get("warnings", []),
                "data_source": data_source,
                "volume_data": volume_data_status,
                "score_detail": score_detail,
            })

        industries = sorted(set(
            c["industry"] for c in self._get_industry_candidates()
        ))

        # 最近 20 个交易日列表（前端时间线用，始终基于最新交易日）
        trading_days = self._get_recent_trading_days(20)

        result = {
            "items": items,
            "market_regime": regime,
            "industries_covered": industries,
            "data_source": data_source,
            "volume_data": volume_data_status,
            "trading_days": trading_days,
            "updated_at": datetime.now().isoformat(),
        }

        _leaderboard_cache[cache_key] = (now, result)
        logger.info(f"[Leaderboard] Done: {len(items)} items, regime={regime}")
        return result

    # ── 1.2 候选股筛选 ──────────────────────────────────────

    def _get_industry_candidates(self) -> List[Dict]:
        """从 stock_pool.db 查询每行业市值前 3 名，排除 ST/退市。"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ts_code, symbol, name, industry, market_cap
                FROM stock_pool
                WHERE is_st = 0 AND market_cap > 0 AND industry IS NOT NULL AND industry != ''
            """)
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()

        by_industry: Dict[str, list] = {}
        for r in rows:
            ind = r.get("industry", "") or "其他"
            by_industry.setdefault(ind, []).append(r)

        candidates = []
        for ind, stocks in by_industry.items():
            stocks.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
            candidates.extend(stocks[:3])

        return candidates

    # ── 1.3 腾讯实时行情批量 ─────────────────────────────────

    @staticmethod
    def _ts_code_to_tencent_symbol(ts_code: str) -> str:
        """000001.SZ → sz000001, 600519.SH → sh600519"""
        code, exchange = ts_code.split(".") if "." in ts_code else (ts_code.lstrip("SHEZBJ"), "")
        if exchange in ("SH", "BJ"):
            return f"sh{code}"
        return f"sz{code}"

    @staticmethod
    def _tencent_symbol_to_ts_code(tx_sym: str) -> str:
        """sz000001 → 000001.SZ, sh600519 → 600519.SH"""
        if tx_sym.startswith("sh"):
            return f"{tx_sym[2:]}.SH"
        return f"{tx_sym[2:]}.SZ"

    @staticmethod
    def _is_trading_time() -> bool:
        """判断当前是否在 A 股交易时段（周一至周五 9:30-11:30 / 13:00-15:00 北京时间）。"""
        from datetime import timezone, timedelta
        cst = timezone(timedelta(hours=8))
        now = datetime.now(cst)
        if now.weekday() >= 5:
            return False
        t = now.hour * 100 + now.minute
        return (930 <= t <= 1130) or (1300 <= t <= 1500)

    def _fetch_realtime_quotes_batch(self, symbols: List[str]) -> Tuple[Dict[str, dict], str]:
        """腾讯 qt.gtimg.cn 批量获取实时行情（非交易时段降级为日频）。返回 (quotes_dict, data_source)。"""
        # 非交易时段直接降级为日频，避免腾讯返回空数据
        if not self._is_trading_time():
            logger.info("[Leaderboard] Non-trading hours, using daily fallback for quotes")
            return self._fallback_quotes_from_daily(symbols), "tushare_daily"

        tx_symbols = [self._ts_code_to_tencent_symbol(s) for s in symbols]
        quotes: Dict[str, dict] = {}

        try:
            # 分批请求 (腾讯单次上限约 100 只)
            batch_size = 80
            for i in range(0, len(tx_symbols), batch_size):
                batch = tx_symbols[i:i + batch_size]
                url = "http://qt.gtimg.cn/q=" + ",".join(batch)
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode("gbk", errors="replace")

                for line in text.strip().split("\n"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    # var hq_str_sh600519="name,..."
                    var_name, _, fields_str = line.partition("=")
                    tx_sym = var_name.replace("var hq_str_", "").strip()
                    ts_code = self._tencent_symbol_to_ts_code(tx_sym)
                    fields = fields_str.strip('";').split("~")
                    if len(fields) < 40:
                        continue
                    quotes[ts_code] = {
                        "price": float(fields[_TX_PRICE]) if fields[_TX_PRICE] else 0,
                        "change_pct": float(fields[_TX_CHANGE_PCT]) if fields[_TX_CHANGE_PCT] else 0,
                        "turnover_rate": float(fields[_TX_TURNOVER_RATE]) if fields[_TX_TURNOVER_RATE] else 0,
                        "amount": float(fields[_TX_AMOUNT]) * 10000 if fields[_TX_AMOUNT] else 0,  # 万元→元
                        "high": float(fields[_TX_HIGH]) if fields[_TX_HIGH] else 0,
                        "low": float(fields[_TX_LOW]) if fields[_TX_LOW] else 0,
                        "open": float(fields[_TX_OPEN]) if fields[_TX_OPEN] else 0,
                        "pre_close": float(fields[_TX_PRE_CLOSE]) if fields[_TX_PRE_CLOSE] else 0,
                    }
            return quotes, "tencent"

        except Exception as e:
            logger.warning(f"[Leaderboard] Tencent quotes failed: {e}, falling back to Tushare daily")
            return self._fallback_quotes_from_daily(symbols), "tushare"

    def _fallback_quotes_from_daily(self, symbols: List[str]) -> Dict[str, dict]:
        """降级：从 Tushare daily 获取今日行情。"""
        quotes: Dict[str, dict] = {}
        try:
            pro = _get_tushare_pro()
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")
            # 逐批查询
            for i in range(0, len(symbols), 100):
                batch = ",".join(symbols[i:i + 100])
                df = pro.daily(ts_code=batch, start_date=start_date, end_date=end_date)
                if df is None or df.empty:
                    continue
                latest = df.sort_values("trade_date").groupby("ts_code").last()
                for ts_code, row in latest.iterrows():
                    quotes[ts_code] = {
                        "price": float(row["close"]),
                        "change_pct": float(row.get("pct_chg", 0)),
                        "turnover_rate": 0,
                        "amount": float(row["amount"]) * 1000,  # 千元→元
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "open": float(row["open"]),
                        "pre_close": float(row["pre_close"]),
                    }
        except Exception as e:
            logger.error(f"[Leaderboard] Fallback quotes also failed: {e}")
        return quotes

    # ── 1.4 Tushare 技术指标批量 ─────────────────────────────

    @staticmethod
    def _calc_ma(closes: List[float]) -> Dict[str, float]:
        """从收盘价列表计算 MA5/10/20/60（closes 从旧到新）。"""
        n = len(closes)
        return {
            "ma5": round(sum(closes[-5:]) / 5, 2) if n >= 5 else 0,
            "ma10": round(sum(closes[-10:]) / 10, 2) if n >= 10 else 0,
            "ma20": round(sum(closes[-20:]) / 20, 2) if n >= 20 else 0,
            "ma60": round(sum(closes[-60:]) / 60, 2) if n >= 60 else 0,
        }

    @staticmethod
    def _calc_adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14):
        """从 OHLCV 数组计算 ADX(14) / PDI / MDI（数组从旧到新）。返回 (adx, pdi, mdi)。"""
        n = len(closes)
        if n < period + 2:
            return 0.0, 0.0, 0.0

        tr_list, plus_dm_list, minus_dm_list = [], [], []
        for i in range(1, n):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            tr_list.append(tr)
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
            minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        def _wilder_smooth(series: List[float], p: int) -> List[float]:
            atr = sum(series[:p]) / p
            result = [atr]
            for i in range(p, len(series)):
                atr = (atr * (p - 1) + series[i]) / p
                result.append(atr)
            return result

        atr_vals = _wilder_smooth(tr_list, period)
        spdm_vals = _wilder_smooth(plus_dm_list, period)
        smdm_vals = _wilder_smooth(minus_dm_list, period)

        dx_list = []
        for j in range(len(atr_vals)):
            a = atr_vals[j]
            pdi = 100 * spdm_vals[j] / a if a > 0 else 0
            mdi = 100 * smdm_vals[j] / a if a > 0 else 0
            dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) > 0 else 0
            dx_list.append(dx)

        adx = sum(dx_list[:period]) / period
        for i in range(period, len(dx_list)):
            adx = (adx * (period - 1) + dx_list[i]) / period

        pdi_latest = 100 * spdm_vals[-1] / atr_vals[-1] if atr_vals[-1] > 0 else 0
        mdi_latest = 100 * smdm_vals[-1] / atr_vals[-1] if atr_vals[-1] > 0 else 0
        return round(adx, 2), round(pdi_latest, 2), round(mdi_latest, 2)

    def _fetch_indicators_batch(self, symbols: List[str]) -> Dict[str, dict]:
        """Tushare stk_factor 批量获取 MACD/KDJ/RSI + 手工计算 MA/ADX。"""
        indicators: Dict[str, dict] = {}
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=100)).strftime("%Y%m%d")  # ~70 trading days

        try:
            pro = _get_tushare_pro()
            for i in range(0, len(symbols), 100):
                batch = ",".join(symbols[i:i + 100])
                df = pro.stk_factor(ts_code=batch, start_date=start_date, end_date=end_date)
                if df is None or df.empty:
                    continue
                df = df.sort_values("trade_date")
                for ts_code, group in df.groupby("ts_code"):
                    group = group.reset_index(drop=True)
                    latest = group.iloc[-1]
                    closes = [float(v) for v in group["close_qfq"].values if v and float(v) > 0]
                    highs = [float(v) for v in group["high_qfq"].values if v and float(v) > 0]
                    lows = [float(v) for v in group["low_qfq"].values if v and float(v) > 0]
                    ma = self._calc_ma(closes)
                    adx, pdi, mdi = self._calc_adx(highs, lows, closes)
                    indicators[str(ts_code)] = {
                        "trade_date": str(latest.get("trade_date", "")),
                        "close": float(latest.get("close_qfq", 0) or latest.get("close", 0) or 0),
                        "ma5": ma["ma5"], "ma10": ma["ma10"], "ma20": ma["ma20"], "ma60": ma["ma60"],
                        "macd_dif": float(latest.get("macd_dif", 0) or 0),
                        "macd_dea": float(latest.get("macd_dea", 0) or 0),
                        "macd": float(latest.get("macd", 0) or 0),
                        "adx": adx, "pdi": pdi, "mdi": mdi,
                        "rsi6": float(latest.get("rsi_6", 0) or 0),
                        "pe_ttm": 0,  # stk_factor 无 PE，留给后续扩展
                        "vol": float(latest.get("vol", 0) or 0),
                        "amount": float(latest.get("amount", 0) or 0),
                        "volume_ratio": float(latest.get("volume_ratio", 0) or 0),
                    }
        except Exception as e:
            logger.error(f"[Leaderboard] stk_factor batch failed: {e}")
        return indicators


    # ── 1.6 市场状态判别 ────────────────────────────────────

    def _detect_market_regime(self, as_of_date: Optional[str] = None) -> str:
        """从上证综指 000001.SH 的 ADX + MA5/MA20 判别市场状态。

        Args:
            as_of_date: Optional YYYYMMDD date. When provided, uses data up to that date.
        """
        try:
            pro = _get_tushare_pro()
            end_date = as_of_date or datetime.now().strftime("%Y%m%d")
            start_date = (datetime.strptime(end_date, "%Y%m%d") - timedelta(days=100)).strftime("%Y%m%d")
            df = pro.stk_factor(ts_code="000001.SH", start_date=start_date, end_date=end_date)
            if df is None or df.empty:
                return REGIME_TRANSITIONAL
            df = df.sort_values("trade_date")
            closes = [float(v) for v in df["close_qfq"].values if v and float(v) > 0]
            highs = [float(v) for v in df["high_qfq"].values if v and float(v) > 0]
            lows = [float(v) for v in df["low_qfq"].values if v and float(v) > 0]
            ma = self._calc_ma(closes)
            adx, _, _ = self._calc_adx(highs, lows, closes)

            if adx > 25 and ma["ma5"] > ma["ma20"]:
                return REGIME_TRENDING
            elif adx < 20:
                return REGIME_RANGING
            else:
                return REGIME_TRANSITIONAL
        except Exception as e:
            logger.warning(f"[Leaderboard] Market regime detection failed: {e}")
            return REGIME_TRANSITIONAL

    # ── 交易日历 ───────────────────────────────────────────

    @staticmethod
    def _get_recent_trading_days(n: int = 20, as_of_date: Optional[str] = None) -> List[str]:
        """Get the most recent N trading days.

        Args:
            n: Number of trading days to return.
            as_of_date: Optional YYYYMMDD reference date. When provided, returns
                        the N trading days up to and including that date.
        """
        try:
            pro = _get_tushare_pro()
            ref_date = datetime.strptime(as_of_date, "%Y%m%d") if as_of_date else datetime.now()
            end_date = ref_date.strftime("%Y%m%d")
            lookback = n * 3
            start_date = (ref_date - timedelta(days=lookback)).strftime("%Y%m%d")
            df_cal = pro.trade_cal(exchange='SSE', start_date=start_date, end_date=end_date)
            if df_cal is not None and not df_cal.empty:
                open_days = [str(d) for d in df_cal[df_cal['is_open'] == 1]['cal_date'].tolist()]
                open_days.sort(reverse=True)
                return open_days[:n]
        except Exception as e:
            logger.warning(f"[Leaderboard] trade_cal failed: {e}, using weekday fallback")
        # Fallback: skip weekends
        cursor = datetime.strptime(as_of_date, "%Y%m%d") if as_of_date else datetime.now()
        days = []
        while len(days) < n:
            if cursor.weekday() < 5:
                days.append(cursor.strftime("%Y%m%d"))
            cursor -= timedelta(days=1)
        return days

    # ── 历史模式数据获取 ─────────────────────────────────────

    def _historical_quotes(self, symbols: List[str], date: str) -> Dict[str, dict]:
        """从 Tushare daily 表获取指定日期的行情，组装为与腾讯行情相同格式的 dict。"""
        quotes: Dict[str, dict] = {}
        try:
            pro = _get_tushare_pro()
            # 需要前一日 pre_close，所以往前多取几天
            start_date = (datetime.strptime(date, "%Y%m%d") - timedelta(days=5)).strftime("%Y%m%d")
            for i in range(0, len(symbols), 100):
                batch = ",".join(symbols[i:i + 100])
                df = pro.daily(ts_code=batch, start_date=start_date, end_date=date)
                if df is None or df.empty:
                    continue
                df = df.sort_values("trade_date")
                for ts_code, group in df.groupby("ts_code"):
                    group = group.reset_index(drop=True)
                    # 取最后一条（指定日期当天，或最近的交易日）
                    row = group.iloc[-1]
                    if str(row["trade_date"]) != date:
                        continue  # 该股票在指定日期停牌/无交易
                    # pre_close 从当日行获取
                    pre_close = float(row.get("pre_close", 0) or 0)
                    pct = float(row.get("pct_chg", 0) or 0)
                    quotes[str(ts_code)] = {
                        "price": float(row["close"]),
                        "change_pct": pct,
                        "turnover_rate": 0,  # daily 表无换手率
                        "amount": float(row["amount"]) * 1000,  # 千元→元
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "open": float(row["open"]),
                        "pre_close": pre_close,
                    }
        except Exception as e:
            logger.error(f"[Leaderboard] _historical_quotes failed: {e}")
        return quotes

    def _historical_indicators(self, symbols: List[str], date: str) -> Dict[str, dict]:
        """从 Tushare stk_factor_pro 获取指定日期之前的技术指标，计算当日 MA/ADX/MACD/RSI。"""
        indicators: Dict[str, dict] = {}
        end_date = date
        start_date = (datetime.strptime(date, "%Y%m%d") - timedelta(days=100)).strftime("%Y%m%d")

        try:
            pro = _get_tushare_pro()
            for i in range(0, len(symbols), 100):
                batch = ",".join(symbols[i:i + 100])
                df = pro.stk_factor(ts_code=batch, start_date=start_date, end_date=end_date)
                if df is None or df.empty:
                    continue
                df = df.sort_values("trade_date")
                for ts_code, group in df.groupby("ts_code"):
                    group = group.reset_index(drop=True)
                    # 只取到 date 为止的数据
                    mask = group["trade_date"] <= date
                    group = group[mask].reset_index(drop=True)
                    if group.empty:
                        continue
                    latest = group.iloc[-1]
                    closes = [float(v) for v in group["close_qfq"].values if v and float(v) > 0]
                    highs = [float(v) for v in group["high_qfq"].values if v and float(v) > 0]
                    lows = [float(v) for v in group["low_qfq"].values if v and float(v) > 0]
                    ma = self._calc_ma(closes)
                    adx, pdi, mdi = self._calc_adx(highs, lows, closes)
                    indicators[str(ts_code)] = {
                        "trade_date": str(latest.get("trade_date", "")),
                        "close": float(latest.get("close_qfq", 0) or latest.get("close", 0) or 0),
                        "ma5": ma["ma5"], "ma10": ma["ma10"], "ma20": ma["ma20"], "ma60": ma["ma60"],
                        "macd_dif": float(latest.get("macd_dif", 0) or 0),
                        "macd_dea": float(latest.get("macd_dea", 0) or 0),
                        "macd": float(latest.get("macd", 0) or 0),
                        "adx": adx, "pdi": pdi, "mdi": mdi,
                        "rsi6": float(latest.get("rsi_6", 0) or 0),
                        "pe_ttm": 0,
                        "vol": float(latest.get("vol", 0) or 0),
                        "amount": float(latest.get("amount", 0) or 0),
                        "volume_ratio": float(latest.get("volume_ratio", 0) or 0),
                    }
        except Exception as e:
            logger.error(f"[Leaderboard] _historical_indicators failed: {e}")
        return indicators

    def _fetch_daily_bars_batch(
        self, symbols: List[str], end_date: Optional[str] = None
    ) -> Tuple[Dict[str, List[dict]], str]:
        """Tushare daily 批量获取近 20 个交易日 OHLCV。返回 (daily_bars_dict, status)。

        Args:
            end_date: Optional YYYYMMDD. When provided, fetches data up to this date.
        """
        daily_bars: Dict[str, List[dict]] = {}
        ref_end = end_date or datetime.now().strftime("%Y%m%d")
        start_date = (datetime.strptime(ref_end, "%Y%m%d") - timedelta(days=40)).strftime("%Y%m%d")

        try:
            pro = _get_tushare_pro()
            for i in range(0, len(symbols), 100):
                batch = ",".join(symbols[i:i + 100])
                df = pro.daily(ts_code=batch, start_date=start_date, end_date=ref_end)
                if df is None or df.empty:
                    continue
                df = df.sort_values("trade_date")
                for _, row in df.iterrows():
                    ts_code = str(row["ts_code"])
                    if ts_code not in daily_bars:
                        daily_bars[ts_code] = []
                    daily_bars[ts_code].append({
                        "trade_date": str(row["trade_date"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "vol": float(row["vol"]),
                        "amount": float(row["amount"]),
                        "pct_chg": float(row.get("pct_chg", 0) or 0),
                    })
            status = "full"
        except Exception as e:
            logger.warning(f"[Leaderboard] daily batch failed: {e}, degrading to volume_ratio")
            status = "degraded"

        return daily_bars, status

    def _historical_moneyflow(self, top10_symbols: List[str], date: str) -> Dict[str, dict]:
        """从 Tushare moneyflow 日频接口获取指定日期 + 前5日累计资金流向（仅 Top10）。"""
        moneyflows: Dict[str, dict] = {}
        ref_date = datetime.strptime(date, "%Y%m%d")
        start_date = (ref_date - timedelta(days=12)).strftime("%Y%m%d")  # 留足余量覆盖5个交易日
        try:
            pro = _get_tushare_pro()
            for ts_code in top10_symbols:
                try:
                    df = pro.moneyflow(ts_code=ts_code, start_date=start_date, end_date=date)
                    if df is None or df.empty:
                        logger.warning(f"[Leaderboard] _historical_moneyflow {ts_code}: no data")
                        continue
                    df = df.sort_values("trade_date")
                    # 统一转为字符串比较，兼容 tushare 不同版本返回 int/str 的差异
                    df["_td_str"] = df["trade_date"].astype(str)

                    # 当日行
                    day_rows = df[df["_td_str"] == date]
                    if day_rows.empty:
                        logger.warning(f"[Leaderboard] _historical_moneyflow {ts_code}: date {date} not in data, available={df['_td_str'].tolist()}")
                        continue
                    row = day_rows.iloc[-1]
                    code = ts_code.split(".")[0] if "." in ts_code else ts_code.lstrip("SHEZBJ")

                    buy_lg = float(row.get("buy_lg_amount", 0) or 0)      # 大单买入金额(万元)
                    sell_lg = float(row.get("sell_lg_amount", 0) or 0)    # 大单卖出金额(万元)
                    buy_elg = float(row.get("buy_elg_amount", 0) or 0)    # 特大单买入金额(万元)
                    sell_elg = float(row.get("sell_elg_amount", 0) or 0)  # 特大单卖出金额(万元)
                    main_net = (buy_lg + buy_elg - sell_lg - sell_elg) * 10000  # 万元→元

                    # 计算主力净占比：主力净额 / 主力总成交额
                    main_total = (buy_lg + sell_lg + buy_elg + sell_elg)
                    if main_total > 0:
                        main_pct = round((buy_lg + buy_elg - sell_lg - sell_elg) / main_total * 100, 2)
                    else:
                        main_pct = 0.0

                    # 5日累计主力净流入（从查询结果中取 date 之前最近5个交易日）
                    pre_rows = df[df["_td_str"] < date].tail(5)
                    d5_main_net = 0.0
                    for _, pr in pre_rows.iterrows():
                        d5_buy_lg = float(pr.get("buy_lg_amount", 0) or 0)
                        d5_sell_lg = float(pr.get("sell_lg_amount", 0) or 0)
                        d5_buy_elg = float(pr.get("buy_elg_amount", 0) or 0)
                        d5_sell_elg = float(pr.get("sell_elg_amount", 0) or 0)
                        d5_main_net += (d5_buy_lg + d5_buy_elg - d5_sell_lg - d5_sell_elg) * 10000

                    moneyflows[ts_code] = {
                        "symbol": code,
                        "name": "",
                        "price": 0,
                        "change_pct": "0",
                        "turnover_rate": "0",
                        "main_net": main_net,
                        "main_pct": str(main_pct),
                        "d5_main_net": d5_main_net,
                        "d5_main_pct": "0",
                    }
                    time.sleep(0.12)  # QPS 限制
                except Exception as e:
                    logger.warning(f"[Leaderboard] _historical_moneyflow failed for {ts_code}: {e}")
            logger.info(f"[Leaderboard] _historical_moneyflow: got data for {len(moneyflows)}/{len(top10_symbols)} top10 stocks")
        except Exception as e:
            logger.warning(f"[Leaderboard] _historical_moneyflow overall failed: {e}")
        return moneyflows

    # ── 前瞻收益验证 ─────────────────────────────────────────

    def get_forward_returns(self, symbol: str, date: str) -> dict:
        """Return forward-looking returns for a stock from a historical date.

        Computes next-day, 3-day, and 5-day cumulative returns,
        plus 10 daily close prices after the given date for sparkline.
        """
        try:
            pro = _get_tushare_pro()

            # Normalize ts_code
            if "." not in symbol:
                code = symbol.lstrip("SHEZBJ")
                if len(code) == 6:
                    symbol = f"{code}.SH" if code.startswith(("6", "9")) else f"{code}.SZ"

            # Get the most recent trading day to check if date is latest
            ref_date = datetime.strptime(date, "%Y%m%d")
            today_str = datetime.now().strftime("%Y%m%d")
            df_cal = pro.trade_cal(exchange='SSE',
                                   start_date=(datetime.now() - timedelta(days=10)).strftime("%Y%m%d"),
                                   end_date=today_str)
            latest_trading_day = today_str
            if df_cal is not None and not df_cal.empty:
                open_days = df_cal[df_cal['is_open'] == 1]['cal_date'].tolist()
                open_days.sort(reverse=True)
                latest_trading_day = open_days[0] if open_days else today_str

            if date >= latest_trading_day:
                return {
                    "symbol": symbol, "name": "", "benchmark_date": date,
                    "available": False, "warning": "最新交易日尚无前瞻数据",
                    "next_day_pct": None, "day3_pct": None, "day5_pct": None,
                    "sparkline_closes": [], "sparkline_dates": [],
                }

            # Get post-date trading days via trade_cal
            start_cal = (ref_date + timedelta(days=1)).strftime("%Y%m%d")
            end_cal = (ref_date + timedelta(days=30)).strftime("%Y%m%d")
            df_cal = pro.trade_cal(exchange='SSE', start_date=start_cal, end_date=end_cal)
            future_trading_days: List[str] = []
            if df_cal is not None and not df_cal.empty:
                future_trading_days = sorted(str(d) for d in df_cal[df_cal['is_open'] == 1]['cal_date'].tolist())

            if len(future_trading_days) < 1:
                return {
                    "symbol": symbol, "name": "", "benchmark_date": date,
                    "available": False,
                    "warning": "无法获取后续交易日数据",
                    "next_day_pct": None, "day3_pct": None, "day5_pct": None,
                    "sparkline_closes": [], "sparkline_dates": [],
                }

            # Get benchmark close price
            start_daily = (ref_date - timedelta(days=5)).strftime("%Y%m%d")
            end_daily = future_trading_days[-1] if future_trading_days else (ref_date + timedelta(days=20)).strftime("%Y%m%d")
            df = pro.daily(ts_code=symbol, start_date=start_daily, end_date=end_daily)
            if df is None or df.empty:
                return {
                    "symbol": symbol, "name": "", "benchmark_date": date,
                    "available": False, "warning": f"{symbol} 在 {date} 附近无日线数据",
                    "next_day_pct": None, "day3_pct": None, "day5_pct": None,
                    "sparkline_closes": [], "sparkline_dates": [],
                }

            df = df.sort_values("trade_date")
            closed = df.set_index("trade_date")

            # Benchmark close
            if date not in closed.index:
                # Find nearest trading day before date
                prev_dates = [d for d in closed.index if d <= date]
                if not prev_dates:
                    return {"symbol": symbol, "name": "", "benchmark_date": date,
                            "available": False, "warning": "基准日期之前无交易数据",
                            "next_day_pct": None, "day3_pct": None, "day5_pct": None,
                            "sparkline_closes": [], "sparkline_dates": []}
                benchmark_date_actual = prev_dates[-1]
            else:
                benchmark_date_actual = date

            base_close = float(closed.loc[benchmark_date_actual, "close"])

            # Calculate returns for next-day, 3-day, 5-day
            def _get_return(n: int) -> Optional[float]:
                if n > len(future_trading_days):
                    return None
                target_date = future_trading_days[n - 1]
                if target_date not in closed.index:
                    return None
                target_close = float(closed.loc[target_date, "close"])
                return round((target_close - base_close) / base_close * 100, 2)

            next_day_pct = _get_return(1)
            day3_pct = _get_return(3)
            day5_pct = _get_return(5)

            # Sparkline: 10 daily closes after benchmark date
            sparkline_closes: List[float] = []
            sparkline_dates: List[str] = []
            for td in future_trading_days[:10]:
                if td in closed.index:
                    sparkline_closes.append(round(float(closed.loc[td, "close"]), 2))
                    sparkline_dates.append(td)

            # Get stock name from stock_pool
            name = ""
            try:
                conn = sqlite3.connect(self._db_path)
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM stock_pool WHERE ts_code = ?", (symbol,))
                row = cursor.fetchone()
                if row:
                    name = row["name"]
                conn.close()
            except Exception:
                pass

            return {
                "symbol": symbol, "name": name, "benchmark_date": benchmark_date_actual,
                "available": True, "warning": "",
                "next_day_pct": next_day_pct, "day3_pct": day3_pct, "day5_pct": day5_pct,
                "sparkline_closes": sparkline_closes, "sparkline_dates": sparkline_dates,
            }

        except Exception as e:
            logger.error(f"[Leaderboard] get_forward_returns failed for {symbol} @ {date}: {e}")
            return {
                "symbol": symbol, "name": "", "benchmark_date": date,
                "available": False, "warning": str(e),
                "next_day_pct": None, "day3_pct": None, "day5_pct": None,
                "sparkline_closes": [], "sparkline_dates": [],
            }

    # ── 1.7 Round 1 四维评分 ─────────────────────────────────

    def _compute_trend_composite(
        self, candidates: List[Dict], indicators: Dict[str, dict], regime: str
    ) -> Tuple[Dict[str, float], Dict[str, dict]]:
        """趋势综合维度：MA 排列 + MACD 柱力度 + ADX 趋势强度。返回 (scores, details)。"""
        scores: Dict[str, float] = {}
        details: Dict[str, dict] = {}
        is_trending = regime == REGIME_TRENDING
        is_ranging = regime == REGIME_RANGING
        dim_max = 28 if is_trending else 22

        for c in candidates:
            sym = c["ts_code"]
            ind = indicators.get(sym, {})
            if not ind:
                scores[sym] = 0
                continue

            ma5 = ind.get("ma5", 0)
            ma10 = ind.get("ma10", 0)
            ma20 = ind.get("ma20", 0)
            ma60 = ind.get("ma60", 0)
            macd_dif = ind.get("macd_dif", 0)
            macd_dea = ind.get("macd_dea", 0)
            macd_hist = ind.get("macd", 0)
            adx = ind.get("adx", 0)

            # MA 排列层级 (趋势市 0-10, 震荡市 0-8)
            ma_max = 10 if is_trending else 8
            ma_score = 0
            ma_reason = "MA均线不满足多头排列条件"
            if ma5 > ma10 > ma20 > ma60 and ma5 > 0 and ma60 > 0:
                ma_score = ma_max
                ma_reason = "MA5>MA10>MA20>MA60，多头排列完美"
            elif ma5 > ma10 > ma20 and ma5 > 0 and ma20 > 0:
                ma_score = ma_max * 0.8
                ma_reason = "MA5>MA10>MA20，短期多头排列良好"
            elif ma5 > ma20 and ma5 > 0 and ma20 > 0:
                ma_score = ma_max * 0.5
                ma_reason = "MA5>MA20，短期站上中线，排列一般"
            elif ma5 < ma20 and ma5 > 0 and ma20 > 0:
                ma_score = 0
                ma_reason = "MA5<MA20，短期下穿中线，排列偏空"

            # MACD 柱力度 (趋势市 0-10, 震荡市 0-7)
            macd_max = 10 if is_trending else 7
            macd_score = 0
            macd_reason = ""
            if macd_dif > macd_dea:
                if macd_hist > 0:
                    macd_score = macd_max * (0.7 + 0.3 * min(abs(macd_hist) / max(abs(macd_dif), 0.001), 1))
                    macd_reason = "MACD柱状体扩大中，多头动能强"
                else:
                    macd_score = macd_max * 0.3
                    macd_reason = "MACD金叉但柱状体未扩大，动能偏弱"
            else:
                macd_score = max(0, macd_max * 0.2)
                macd_reason = "MACD死叉状态，动能不足"

            # ADX 趋势强度 (趋势市 0-8, 震荡市 0-7)
            adx_max = 8 if is_trending else 7
            adx_score: float = 0
            if adx > 40:
                adx_score = adx_max
                adx_reason = f"ADX={adx:.0f}，处于强趋势区间"
            elif adx >= 25:
                adx_score = adx_max * (adx - 25) / 15 * 0.5 + adx_max * 0.5
                adx_reason = f"ADX={adx:.0f}，处于中等趋势区间"
            elif adx >= 15:
                adx_score = adx_max * 0.3 * (adx - 15) / 10
                adx_reason = f"ADX={adx:.0f}，趋势偏弱"
            else:
                adx_score = 0
                adx_reason = f"ADX={adx:.0f}，无明显趋势"

            sub_scores = [
                {"label": "MA排列层级", "score": round(ma_score, 1), "max": ma_max, "reason": ma_reason},
                {"label": "MACD柱力度", "score": round(macd_score, 1), "max": macd_max, "reason": macd_reason},
                {"label": "ADX趋势强度", "score": round(adx_score, 1), "max": adx_max, "reason": adx_reason},
            ]

            score = ma_score + macd_score + adx_score

            # 趋势启动加分 (趋势市: ADX>25 + MACD 金叉 ≤5日)
            trend_boost = 0
            if is_trending and adx > 25:
                if macd_dif > macd_dea and macd_dif - macd_dea < abs(macd_hist) * 2:
                    trend_boost = 2
                    score += trend_boost
                    sub_scores.append({"label": "趋势启动", "score": float(trend_boost), "max": 2, "reason": "ADX>25且MACD近期金叉，趋势启动信号"})

            # 震荡市折扣
            if is_ranging:
                discount_applied = False
                if adx < 15:
                    score *= 0.6
                    discount_applied = True
                elif adx < 20:
                    score *= 0.8
                    discount_applied = True
                if discount_applied:
                    adx_reason += "，震荡市ADX低，得分折扣"

            final_score = max(0, min(score, dim_max))
            scores[sym] = final_score
            details[sym] = {
                "label": "趋势综合",
                "score": round(final_score, 1),
                "max": dim_max,
                "sub_scores": sub_scores,
            }

        return scores, details

    def _compute_volume_price(
        self, candidates: List[Dict], indicators: Dict[str, dict],
        daily_bars: Dict[str, List[dict]], regime: str
    ) -> Tuple[Dict[str, float], Dict[str, dict]]:
        """量价配合维度。返回 (scores, details)。"""
        scores: Dict[str, float] = {}
        details: Dict[str, dict] = {}
        is_trending = regime == REGIME_TRENDING
        dim_max = 15 if is_trending else 18

        for c in candidates:
            sym = c["ts_code"]
            bars = daily_bars.get(sym, [])
            ind = indicators.get(sym, {})

            if not bars and not ind:
                scores[sym] = 0
                continue

            # 量价趋势匹配度 (趋势市 0-7, 震荡市 0-8)
            match_max = 7 if is_trending else 8
            match_score: float = 0
            match_reason = ""
            if len(bars) >= 10:
                recent = bars[-10:]
                up_vols = [b["vol"] for b in recent if b.get("pct_chg", 0) > 0]
                down_vols = [b["vol"] for b in recent if b.get("pct_chg", 0) < 0]
                if down_vols and sum(down_vols) > 0:
                    ratio = sum(up_vols) / sum(down_vols)
                    if ratio > 1.5:
                        match_score = match_max
                        match_reason = f"上涨放量/下跌缩量比={ratio:.2f}，量价配合优异"
                    elif ratio > 1.0:
                        match_score = match_max * 0.6
                        match_reason = f"上涨放量/下跌缩量比={ratio:.2f}，量价配合良好"
                    elif ratio > 0.8:
                        match_score = match_max * 0.3
                        match_reason = f"上涨放量/下跌缩量比={ratio:.2f}，量价配合一般"
                    else:
                        match_score = match_max * 0.1
                        match_reason = f"上涨放量/下跌缩量比={ratio:.2f}，量价配合不佳"
                elif up_vols:
                    match_score = match_max * 0.7
                    match_reason = "近10日仅上涨日有成交记录"
            else:
                vr = ind.get("volume_ratio", 1.0)
                match_score = match_max * min(vr / 2.0, 1.0) * 0.7
                match_reason = f"使用量比={vr:.1f}估算量价匹配，数据降级"

            # 突破放量比 (趋势市 0-5, 震荡市 0-6)
            breakout_max = 5 if is_trending else 6
            breakout_score: float = 0
            breakout_reason = ""
            if len(bars) >= 20 and ind.get("ma20", 0) > 0:
                avg_vol_20 = sum(b["vol"] for b in bars[-20:]) / 20
                today_vol = bars[-1]["vol"] if bars else ind.get("vol", 0)
                close = bars[-1]["close"] if bars else ind.get("close", 0)
                ma20 = ind.get("ma20", 0)
                if close > ma20 and today_vol > 0 and avg_vol_20 > 0:
                    ratio = today_vol / avg_vol_20
                    if ratio > 2.0:
                        breakout_score = breakout_max
                        breakout_reason = f"突破MA20时放量{ratio:.1f}倍，强烈突破信号"
                    elif ratio > 1.5:
                        breakout_score = breakout_max * 0.7
                        breakout_reason = f"突破MA20时放量{ratio:.1f}倍，温和突破"
                    elif ratio > 1.0:
                        breakout_score = breakout_max * 0.3
                        breakout_reason = f"突破MA20时放量{ratio:.1f}倍，突破弱"
                else:
                    breakout_reason = "未有效突破MA20"
            else:
                breakout_reason = "数据不足(需20日线数据)"

            # 缩量回调健康度 (趋势市 0-3, 震荡市 0-4)
            pullback_max = 3 if is_trending else 4
            pullback_score: float = 0
            pullback_reason = ""
            if len(bars) >= 20 and ind.get("close", 0) > 0:
                avg_vol_20 = sum(b["vol"] for b in bars[-20:]) / 20
                today_vol = bars[-1]["vol"] if bars else ind.get("vol", 0)
                today_pct = bars[-1].get("pct_chg", 0) if bars else 0
                if today_pct < 0 and today_vol > 0 and avg_vol_20 > 0:
                    ratio = today_vol / avg_vol_20
                    if ratio < 0.7:
                        pullback_score = pullback_max
                        pullback_reason = f"缩量回调{ratio:.1f}倍均量，回调健康"
                    elif ratio < 1.0:
                        pullback_score = pullback_max * 0.5
                        pullback_reason = f"回调量比{ratio:.1f}，缩量一般"
                else:
                    pullback_reason = "今日上涨或无回调"
            else:
                pullback_reason = "数据不足(需20日线数据)"

            score = match_score + breakout_score + pullback_score
            final_score = max(0, min(score, dim_max))
            scores[sym] = final_score
            details[sym] = {
                "label": "量价配合",
                "score": round(final_score, 1),
                "max": dim_max,
                "sub_scores": [
                    {"label": "量价匹配度", "score": round(match_score, 1), "max": match_max, "reason": match_reason},
                    {"label": "突破放量比", "score": round(breakout_score, 1), "max": breakout_max, "reason": breakout_reason},
                    {"label": "缩量回调健康度", "score": round(pullback_score, 1), "max": pullback_max, "reason": pullback_reason},
                ],
            }

        return scores, details

    def _compute_industry_relative_strength(
        self, candidates: List[Dict], quotes: Dict[str, dict],
        daily_bars: Dict[str, List[dict]], regime: str
    ) -> Tuple[Dict[str, float], Dict[str, dict]]:
        """行业相对强度维度。返回 (scores, details)。"""
        scores: Dict[str, float] = {}
        details: Dict[str, dict] = {}
        is_trending = regime == REGIME_TRENDING
        dim_max = 17 if is_trending else 20
        sub_5d_max = 6 if is_trending else 7
        contrib_max = 4 if is_trending else 6

        # 按行业分组
        by_industry: Dict[str, List[Dict]] = {}
        for c in candidates:
            ind = c.get("industry", "其他")
            by_industry.setdefault(ind, []).append(c)

        for c in candidates:
            sym = c["ts_code"]
            ind_name = c.get("industry", "其他")
            peers = by_industry.get(ind_name, [])
            q = quotes.get(sym, {})

            stock_pct = q.get("change_pct", 0)

            # 行业候选股均值
            peer_pcts = [quotes.get(p["ts_code"], {}).get("change_pct", 0) for p in peers]
            peer_avg_pct = sum(peer_pcts) / len(peer_pcts) if peer_pcts else 0

            excess_1d = stock_pct - peer_avg_pct

            # 1日超额收益 (0-7)
            sub_1d: float = 0
            reason_1d = ""
            if excess_1d > 2:
                sub_1d = 7
                reason_1d = f"当日超额{excess_1d:+.1f}%，远超行业均值"
            elif excess_1d > 1:
                sub_1d = 5
                reason_1d = f"当日超额{excess_1d:+.1f}%，显著跑赢行业"
            elif excess_1d > 0:
                sub_1d = 3
                reason_1d = f"当日超额{excess_1d:+.1f}%，略超行业"
            elif excess_1d > -1:
                sub_1d = 1
                reason_1d = f"当日超额{excess_1d:+.1f}%，与行业持平"
            else:
                reason_1d = f"当日超额{excess_1d:+.1f}%，跑输行业"

            # 5日累计超额 (趋势市 0-6, 震荡市 0-7)
            sub_5d: float = 0
            reason_5d = ""
            bars = daily_bars.get(sym, [])
            if len(bars) >= 5:
                stock_5d = sum(b.get("pct_chg", 0) for b in bars[-5:])
                peer_5d_pcts = []
                for p in peers:
                    p_bars = daily_bars.get(p["ts_code"], [])
                    peer_5d_pcts.append(sum(b.get("pct_chg", 0) for b in p_bars[-5:]) if len(p_bars) >= 5 else 0)
                peer_5d_avg = sum(peer_5d_pcts) / len(peer_5d_pcts) if peer_5d_pcts else 0
                excess_5d = stock_5d - peer_5d_avg
                if excess_5d > 5:
                    sub_5d = sub_5d_max
                    reason_5d = f"5日累计超额{excess_5d:+.1f}%，持续领跑行业"
                elif excess_5d > 2:
                    sub_5d = sub_5d_max * 0.6
                    reason_5d = f"5日累计超额{excess_5d:+.1f}%，跑赢行业"
                elif excess_5d > 0:
                    sub_5d = sub_5d_max * 0.3
                    reason_5d = f"5日累计超额{excess_5d:+.1f}%，略超行业"
                else:
                    reason_5d = "5日涨幅未跑赢行业均值"
            else:
                reason_5d = "5日数据不足"

            # 成交额贡献度 (趋势市 0-4, 震荡市 0-6)
            sub_contrib: float = 0
            reason_contrib = ""
            my_amount = q.get("amount", 0)
            total_amount = sum(quotes.get(p["ts_code"], {}).get("amount", 0) for p in peers)
            if total_amount > 0:
                share = my_amount / total_amount
                if share > 0.5:
                    sub_contrib = contrib_max
                    reason_contrib = f"成交额占行业{share:.0%}，行业龙头地位显著"
                elif share > 0.3:
                    sub_contrib = contrib_max * 0.7
                    reason_contrib = f"成交额占行业{share:.0%}，行业主力品种"
                elif share > 0.2:
                    sub_contrib = contrib_max * 0.4
                    reason_contrib = f"成交额占行业{share:.0%}，行业活跃品种"
                else:
                    reason_contrib = f"成交额占行业{share:.0%}，关注度一般"
            else:
                reason_contrib = "行业成交额数据不足"

            score = sub_1d + sub_5d + sub_contrib
            final_score = max(0, min(score, dim_max))
            scores[sym] = final_score
            details[sym] = {
                "label": "行业相对强度",
                "score": round(final_score, 1),
                "max": dim_max,
                "sub_scores": [
                    {"label": "1日超额收益", "score": round(sub_1d, 1), "max": 7, "reason": reason_1d},
                    {"label": "5日累计超额", "score": round(sub_5d, 1), "max": sub_5d_max, "reason": reason_5d},
                    {"label": "成交额贡献度", "score": round(sub_contrib, 1), "max": contrib_max, "reason": reason_contrib},
                ],
            }

        return scores, details

    def _compute_price_residual(
        self, candidates: List[Dict], quotes: Dict[str, dict],
        indicators: Dict[str, dict], regime: str
    ) -> Tuple[Dict[str, float], Dict[str, dict]]:
        """价格残差维度（风险指标）。返回 (scores, details)。"""
        scores: Dict[str, float] = {}
        details: Dict[str, dict] = {}
        is_trending = regime == REGIME_TRENDING
        dim_max = 15 if is_trending else 18

        for c in candidates:
            sym = c["ts_code"]
            q = quotes.get(sym, {})
            ind = indicators.get(sym, {})

            if not q:
                scores[sym] = 0
                continue

            close = q.get("price", 0)
            ma20 = ind.get("ma20", 0)

            # MA20 乖离率倒U型 (趋势市 0-6, 震荡市 0-8)
            dev_max = 6 if is_trending else 8
            dev_score: float = 0
            dev_reason = ""
            dev_optimal_low = 3 if is_trending else 2
            dev_optimal_high = 10 if is_trending else 8
            if ma20 > 0 and close > 0:
                deviation = abs(close - ma20) / ma20 * 100
                if dev_optimal_low <= deviation <= dev_optimal_high:
                    dev_score = dev_max
                    dev_reason = f"乖离率{deviation:.1f}%，处于{dev_optimal_low}-{dev_optimal_high}%最优区间"
                elif deviation < dev_optimal_low:
                    dev_score = dev_max * (deviation / dev_optimal_low)
                    dev_reason = f"乖离率{deviation:.1f}%，偏离过小，趋势力度不足"
                elif deviation < 15:
                    dev_score = dev_max * (15 - deviation) / (15 - dev_optimal_high)
                    dev_reason = f"乖离率{deviation:.1f}%，偏高但尚可接受"
                else:
                    dev_score = 0
                    dev_reason = f"乖离率{deviation:.1f}%，严重偏离均线"
            else:
                dev_reason = "MA20或价格数据不足"

            # 超额涨幅 (趋势市 0-6, 震荡市 0-7)
            excess_max = 6 if is_trending else 7
            stock_pct = q.get("change_pct", 0)
            excess_score: float = 0
            excess_reason = ""
            if stock_pct > 2:
                excess_score = excess_max
                excess_reason = f"当日涨幅{stock_pct:+.1f}%，动能强劲"
            elif stock_pct > 1:
                excess_score = excess_max * 0.7
                excess_reason = f"当日涨幅{stock_pct:+.1f}%，动能良好"
            elif stock_pct > 0:
                excess_score = excess_max * 0.4
                excess_reason = f"当日涨幅{stock_pct:+.1f}%，动能一般"
            else:
                excess_reason = f"当日涨幅{stock_pct:+.1f}%，动能不足"

            # 非尾盘拉升验证 (0-3)
            tail_score: float = 3
            tail_reason = "未检测到尾盘拉升迹象"
            open_p = q.get("open", 0)
            high_p = q.get("high", 0)
            pre_close = q.get("pre_close", 0)
            if close > pre_close and open_p > 0 and high_p > 0:
                close_pct_of_range = (close - open_p) / max(high_p - open_p, 0.01)
                if close_pct_of_range > 0.9:
                    tail_score = 1
                    tail_reason = "疑似尾盘拉升(收盘价接近最高价)"

            score = dev_score + excess_score + tail_score
            final_score = max(0, min(score, dim_max))
            scores[sym] = final_score
            details[sym] = {
                "label": "价格残差",
                "score": round(final_score, 1),
                "max": dim_max,
                "sub_scores": [
                    {"label": "MA20乖离率", "score": round(dev_score, 1), "max": dev_max, "reason": dev_reason},
                    {"label": "超额涨幅", "score": round(excess_score, 1), "max": excess_max, "reason": excess_reason},
                    {"label": "尾盘拉升验证", "score": round(tail_score, 1), "max": 3, "reason": tail_reason},
                ],
            }

        return scores, details

    # ── 1.8 Round 2 资金补算 ─────────────────────────────────

    def _fetch_top10_moneyflow(self, top10_symbols: List[str]) -> Dict[str, dict]:
        """东方财富实时接口，串行逐只获取 Top10 资金流向。"""
        moneyflows: Dict[str, dict] = {}
        for ts_code in top10_symbols:
            try:
                flow = self._query_stock_flow_eastmoney(ts_code)
                if flow:
                    moneyflows[ts_code] = flow
                time.sleep(0.15)
            except Exception as e:
                logger.warning(f"[Leaderboard] Moneyflow failed for {ts_code}: {e}")
        return moneyflows

    @staticmethod
    def _query_stock_flow_eastmoney(ts_code: str) -> Optional[dict]:
        """查询单只股票实时资金流（东方财富 api/qt/stock/get）。"""
        code = ts_code.split(".")[0] if "." in ts_code else ts_code.lstrip("SHEZBJ")
        secid = f"1.{code}" if code.startswith(("6", "9")) else f"0.{code}"
        fields = ("f12,f14,f2,f3,f170,"
                  "f137,f140,f143,f146,f149,f193,f194,f195,f196,f197,"
                  "f434,f435,f436,f437,f438,f454,f455,f456,f457,f458,"
                  "f459,f461,f463,f465,f467,f460,f462,f464,f466,f468")

        em_proxy = os.environ.get("EM_PROXY_URL", "")
        try:
            if em_proxy:
                import requests as req
                resp = req.get(f"{em_proxy}/api/qt/stock/get?secid={secid}&fields={fields}", timeout=10)
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
            return {
                "symbol": code,
                "name": str(d.get("f14", "")),
                "price": float(d.get("f2", 0) or 0),
                "change_pct": str(round(float(d.get("f3", 0) or 0) / 100, 2)),
                "turnover_rate": str(round(float(d.get("f170", 0) or 0) / 100, 2)) if d.get("f170") else "0",
                "main_net": float(d.get("f137", 0) or 0),
                "main_pct": str(round(float(d.get("f193", 0) or 0) / 100, 2)),
                "d5_main_net": float(d.get("f434", 0) or 0),
                "d5_main_pct": str(round(float(d.get("f454", 0) or 0) / 100, 2)),
            }
        except Exception:
            return None

    def _compute_capital_persistence(
        self, candidates: List[Dict], moneyflows: Dict[str, dict], regime: str
    ) -> Tuple[Dict[str, Optional[float]], Dict[str, dict]]:
        """资金持续性维度得分（仅 Top10）。返回 (scores, details)，无数据时 score 为 None。"""
        scores: Dict[str, Optional[float]] = {}
        details: Dict[str, dict] = {}
        is_trending = regime == REGIME_TRENDING
        dim_max = 25 if is_trending else 22

        for c in candidates:
            sym = c["ts_code"]
            mf = moneyflows.get(sym)

            if not mf:
                scores[sym] = None
                continue

            # 当日主力净流入 / 流通市值归一化 (趋势市 0-10, 震荡市 0-8)
            main_abs_max = 10 if is_trending else 8
            main_net = mf.get("main_net", 0)
            market_cap = c.get("market_cap", 0) or 1
            flow_ratio = main_net / (market_cap * 1e8) if market_cap > 0 else 0
            if flow_ratio > 0.003:
                main_score = main_abs_max
                main_reason = f"主力净流入/流通市值={flow_ratio:.4f}，大资金净流入"
            elif flow_ratio > 0:
                main_score = main_abs_max * flow_ratio / 0.003
                main_reason = f"主力净流入/流通市值={flow_ratio:.4f}，温和净流入"
            else:
                main_score = 0
                main_reason = "主力净流出或零流入"

            # 主力净占比 (趋势市 0-8, 震荡市 0-7)
            pct_max = 8 if is_trending else 7
            main_pct = float(str(mf.get("main_pct", "0")).replace("%", ""))
            if main_pct > 10:
                pct_score = pct_max
                pct_reason = f"主力净占比{main_pct:.1f}%，主力高度控盘"
            elif main_pct > 0:
                pct_score = pct_max * main_pct / 10
                pct_reason = f"主力净占比{main_pct:.1f}%，主力偏多"
            else:
                pct_score = 0
                pct_reason = f"主力净占比{main_pct:.1f}%，主力偏空"

            # 5日累计主力净流入 / 流通市值 (0-7)
            d5_max = 7
            d5_main_net = mf.get("d5_main_net", 0)
            d5_ratio = d5_main_net / (market_cap * 1e8) if market_cap > 0 else 0
            if d5_ratio > 0.005:
                d5_score = d5_max
                d5_reason = f"5日累计流入/市值={d5_ratio:.4f}，持续大额流入"
            elif d5_ratio > 0:
                d5_score = d5_max * d5_ratio / 0.005
                d5_reason = f"5日累计流入/市值={d5_ratio:.4f}，持续流入"
            else:
                d5_score = 0
                d5_reason = "5日主力净流出或零流入"

            score = main_score + pct_score + d5_score
            final_score = max(0, min(score, dim_max))
            scores[sym] = final_score
            details[sym] = {
                "label": "资金持续性",
                "score": round(final_score, 1),
                "max": dim_max,
                "sub_scores": [
                    {"label": "主力净流入/市值", "score": round(main_score, 1), "max": main_abs_max, "reason": main_reason},
                    {"label": "主力净占比", "score": round(pct_score, 1), "max": pct_max, "reason": pct_reason},
                    {"label": "5日累计流入/市值", "score": round(d5_score, 1), "max": d5_max, "reason": d5_reason},
                ],
            }

        return scores, details

    # ── 1.9 硬过滤 ──────────────────────────────────────────

    def _apply_hard_filters(
        self, candidates: List[Dict], quotes: Dict[str, dict],
        indicators: Optional[Dict[str, dict]] = None
    ) -> Tuple[List[Dict], List[Dict]]:
        """硬过滤：日成交额<1亿排除、一字板标记不可交易、PE>200标记风险。"""
        kept = []
        excluded = []

        for c in candidates:
            sym = c["ts_code"]
            q = quotes.get(sym, {})
            ind = (indicators or {}).get(sym, {})

            # 日成交额 < 1亿 → 排除
            amount = q.get("amount", 0)
            if amount > 0 and amount < 1e8:
                excluded.append(c)
                continue

            c.setdefault("warnings", [])

            # 一字板涨停检测
            open_p = q.get("open", 0)
            high_p = q.get("high", 0)
            close_p = q.get("price", 0)
            change_pct = q.get("change_pct", 0)
            if (open_p == high_p == close_p and open_p > 0 and change_pct > 9.5):
                c["warnings"].append("untradeable")

            # PE > 200 高估值标记
            pe = ind.get("pe_ttm", 0)
            if pe > 200:
                c["warnings"].append("high_pe")

            kept.append(c)

        return kept, excluded

    # ── 1.10 惩罚系数 ────────────────────────────────────────

    def _apply_penalties(self, candidates: List[Dict], indicators: Dict[str, dict]):
        """维度地板惩罚 + 过热预警。"""
        for c in candidates:
            sym = c["ts_code"]
            ind = indicators.get(sym, {})

            # 维度地板惩罚：任一维度 < 20% 满分 → 总分 × 0.7
            dims = [
                ("trend_score", 28),
                ("volume_price_score", 15),
                ("industry_relative_score", 17),
                ("price_residual_score", 15),
            ]
            for field, max_val in dims:
                val = c.get(field, 0)
                if val < max_val * 0.2:
                    c["composite_score"] = round(c.get("composite_score", 0) * 0.7, 1)
                    c.setdefault("warnings", []).append("dimension_floor")
                    break

            # 过热预警：RSI6 > 90 + 乖离 > 15%
            rsi = ind.get("rsi6", 0)
            close = ind.get("close", 0)
            ma20 = ind.get("ma20", 0)
            if rsi > 90 and ma20 > 0 and close > 0:
                deviation = abs(close - ma20) / ma20 * 100
                if deviation > 15:
                    c["price_residual_score"] = max(0, c.get("price_residual_score", 0) - 3)
                    c.setdefault("warnings", []).append("overheat")
