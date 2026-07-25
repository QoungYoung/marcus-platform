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
    ) -> dict:
        """Main entry: fetch, score, rank, cache."""
        cache_key = f"{sort_by}:{industry or ''}"
        now = time.time()

        if not refresh and cache_key in _leaderboard_cache:
            ts, data = _leaderboard_cache[cache_key]
            if now - ts < _CACHE_TTL:
                logger.info(f"[Leaderboard] Cache hit for '{cache_key}' ({now - ts:.0f}s ago)")
                return data

        logger.info("[Leaderboard] Round 1: fetching ~330 candidates + 3 batch APIs...")

        # 1.2 候选股筛选
        candidates = self._get_industry_candidates()
        if not candidates:
            return {"items": [], "market_regime": REGIME_TRANSITIONAL, "industries_covered": [],
                    "data_source": "tencent", "volume_data": "full", "updated_at": datetime.now().isoformat()}

        symbols = [c["ts_code"] for c in candidates]
        logger.info(f"[Leaderboard] {len(symbols)} candidates from {len(set(c['industry'] for c in candidates))} industries")

        # 1.6 市场状态
        regime = self._detect_market_regime()
        logger.info(f"[Leaderboard] Market regime: {regime}")

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
        trend_scores = self._compute_trend_composite(candidates, indicators, regime)
        volume_scores = self._compute_volume_price(candidates, indicators, daily_bars, regime)
        industry_scores = self._compute_industry_relative_strength(candidates, quotes, daily_bars, regime)
        residual_scores = self._compute_price_residual(candidates, quotes, indicators, regime)

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
        moneyflows = self._fetch_top10_moneyflow([c["ts_code"] for c in top10])
        capital_scores = self._compute_capital_persistence(top10, moneyflows, regime)

        for c in top10:
            sym = c["ts_code"]
            new_cap = capital_scores.get(sym)
            if new_cap is not None and moneyflows.get(sym):
                c["capital_score"] = round(new_cap, 1)
                c["capital_data"] = "available"
            elif new_cap is not None:
                c["capital_score"] = round(new_cap, 1)
                c["capital_data"] = "unavailable"

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

        # 构建响应
        items = []
        for c in candidates:
            q = quotes.get(c["ts_code"], {})
            ind = indicators.get(c["ts_code"], {})
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
                "capital_score": c.get("capital_score", 0),
                "capital_data": c.get("capital_data", "neutral"),
                "warnings": c.get("warnings", []),
                "data_source": data_source,
                "volume_data": volume_data_status,
            })

        industries = sorted(set(
            c["industry"] for c in self._get_industry_candidates()
        ))

        result = {
            "items": items,
            "market_regime": regime,
            "industries_covered": industries,
            "data_source": data_source,
            "volume_data": volume_data_status,
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

    def _fetch_realtime_quotes_batch(self, symbols: List[str]) -> Tuple[Dict[str, dict], str]:
        """腾讯 qt.gtimg.cn 批量获取实时行情。返回 (quotes_dict, data_source)。"""
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

    def _fetch_indicators_batch(self, symbols: List[str]) -> Dict[str, dict]:
        """Tushare stk_factor_pro 批量获取 MA/MACD/ADX/RSI/PE。"""
        indicators: Dict[str, dict] = {}
        try:
            pro = _get_tushare_pro()
            # stk_factor_pro 批量逗号分隔
            for i in range(0, len(symbols), 100):
                batch = ",".join(symbols[i:i + 100])
                df = pro.stk_factor_pro(ts_code=batch, limit=len(symbols[i:i + 100]))
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    ts_code = str(row["ts_code"])
                    indicators[ts_code] = {
                        "trade_date": str(row.get("trade_date", "")),
                        "close": float(row.get("close_qfq", 0) or 0),
                        "ma5": float(row.get("ma_qfq_5", 0) or 0),
                        "ma10": float(row.get("ma_qfq_10", 0) or 0),
                        "ma20": float(row.get("ma_qfq_20", 0) or 0),
                        "ma60": float(row.get("ma_qfq_60", 0) or 0),
                        "macd_dif": float(row.get("macd_dif_qfq", 0) or 0),
                        "macd_dea": float(row.get("macd_dea_qfq", 0) or 0),
                        "macd": float(row.get("macd_qfq", 0) or 0),  # histogram
                        "adx": float(row.get("dmi_adx_qfq", 0) or 0),
                        "pdi": float(row.get("dmi_pdi_qfq", 0) or 0),
                        "mdi": float(row.get("dmi_mdi_qfq", 0) or 0),
                        "rsi6": float(row.get("rsi_qfq_6", 0) or 0),
                        "pe_ttm": float(row.get("pe_ttm", 0) or 0),
                        "vol": float(row.get("vol", 0) or 0),
                        "amount": float(row.get("amount", 0) or 0),
                        "volume_ratio": float(row.get("volume_ratio", 0) or 0),
                    }
        except Exception as e:
            logger.error(f"[Leaderboard] stk_factor_pro batch failed: {e}")
        return indicators

    # ── 1.5 Tushare 日线批量 ─────────────────────────────────

    def _fetch_daily_bars_batch(self, symbols: List[str]) -> Tuple[Dict[str, List[dict]], str]:
        """Tushare daily 批量获取近 20 个交易日 OHLCV。返回 (daily_bars_dict, status)。"""
        daily_bars: Dict[str, List[dict]] = {}
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d")  # 40 calendar days ≈ 20 trading days

        try:
            pro = _get_tushare_pro()
            for i in range(0, len(symbols), 100):
                batch = ",".join(symbols[i:i + 100])
                df = pro.daily(ts_code=batch, start_date=start_date, end_date=end_date)
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

    # ── 1.6 市场状态判别 ────────────────────────────────────

    def _detect_market_regime(self) -> str:
        """从上证综指 000001.SH 的 ADX + MA5/MA20 判别市场状态。"""
        try:
            pro = _get_tushare_pro()
            df = pro.stk_factor_pro(ts_code="000001.SH", limit=5)
            if df is None or df.empty:
                return REGIME_TRANSITIONAL

            row = df.iloc[0]
            adx = float(row.get("dmi_adx_qfq", 0) or 0)
            ma5 = float(row.get("ma_qfq_5", 0) or 0)
            ma20 = float(row.get("ma_qfq_20", 0) or 0)

            if adx > 25 and ma5 > ma20:
                return REGIME_TRENDING
            elif adx < 20:
                return REGIME_RANGING
            else:
                return REGIME_TRANSITIONAL
        except Exception as e:
            logger.warning(f"[Leaderboard] Market regime detection failed: {e}")
            return REGIME_TRANSITIONAL

    # ── 1.7 Round 1 四维评分 ─────────────────────────────────

    def _compute_trend_composite(
        self, candidates: List[Dict], indicators: Dict[str, dict], regime: str
    ) -> Dict[str, float]:
        """趋势综合维度：MA 排列 + MACD 柱力度 + ADX 趋势强度。"""
        scores: Dict[str, float] = {}
        is_trending = regime == REGIME_TRENDING
        is_ranging = regime == REGIME_RANGING

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
            macd_hist = ind.get("macd", 0)  # DIF-DEA
            adx = ind.get("adx", 0)
            pdi = ind.get("pdi", 0)
            mdi = ind.get("mdi", 0)

            # MA 排列层级 (趋势市 0-10, 震荡市 0-8)
            ma_max = 10 if is_trending else 8
            ma_score = 0
            if ma5 > ma10 > ma20 > ma60 and ma5 > 0 and ma60 > 0:
                ma_score = ma_max
            elif ma5 > ma10 > ma20 and ma5 > 0 and ma20 > 0:
                ma_score = ma_max * 0.8
            elif ma5 > ma20 and ma5 > 0 and ma20 > 0:
                ma_score = ma_max * 0.5
            elif ma5 < ma20 and ma5 > 0 and ma20 > 0:
                ma_score = 0

            # MACD 柱力度 (趋势市 0-10, 震荡市 0-7)
            macd_max = 10 if is_trending else 7
            macd_score = 0
            if macd_dif > macd_dea:
                # 柱状体扩大中
                if macd_hist > 0:
                    macd_score = macd_max * (0.7 + 0.3 * min(abs(macd_hist) / max(abs(macd_dif), 0.001), 1))
                else:
                    macd_score = macd_max * 0.3
            else:
                macd_score = max(0, macd_max * 0.2)

            # ADX 趋势强度 (趋势市 0-8, 震荡市 0-7)
            adx_max = 8 if is_trending else 7
            if adx > 40:
                adx_score = adx_max
            elif adx >= 25:
                adx_score = adx_max * (adx - 25) / 15 * 0.5 + adx_max * 0.5
            elif adx >= 15:
                adx_score = adx_max * 0.3 * (adx - 15) / 10
            else:
                adx_score = 0

            score = ma_score + macd_score + adx_score

            # 趋势启动加分 (趋势市: ADX>25 + MACD 金叉 ≤5日)
            if is_trending and adx > 25:
                if macd_dif > macd_dea and macd_dif - macd_dea < abs(macd_hist) * 2:
                    score += 2

            # 震荡市折扣
            if is_ranging:
                if adx < 15:
                    score *= 0.6
                elif adx < 20:
                    score *= 0.8

            scores[sym] = max(0, min(score, 28 if is_trending else 22))

        return scores

    def _compute_volume_price(
        self, candidates: List[Dict], indicators: Dict[str, dict],
        daily_bars: Dict[str, List[dict]], regime: str
    ) -> Dict[str, float]:
        """量价配合维度。"""
        scores: Dict[str, float] = {}
        is_trending = regime == REGIME_TRENDING

        for c in candidates:
            sym = c["ts_code"]
            bars = daily_bars.get(sym, [])
            ind = indicators.get(sym, {})

            if not bars and not ind:
                scores[sym] = 0
                continue

            # 量价趋势匹配度 (趋势市 0-7, 震荡市 0-8)
            match_max = 7 if is_trending else 8
            match_score = 0
            if len(bars) >= 10:
                recent = bars[-10:]
                up_vols = [b["vol"] for b in recent if b.get("pct_chg", 0) > 0]
                down_vols = [b["vol"] for b in recent if b.get("pct_chg", 0) < 0]
                if down_vols and sum(down_vols) > 0:
                    ratio = sum(up_vols) / sum(down_vols)
                    if ratio > 1.5:
                        match_score = match_max
                    elif ratio > 1.0:
                        match_score = match_max * 0.6
                    elif ratio > 0.8:
                        match_score = match_max * 0.3
                    else:
                        match_score = match_max * 0.1
                elif up_vols:
                    match_score = match_max * 0.7
            else:
                # 降级：用 volume_ratio 估算
                vr = ind.get("volume_ratio", 1.0)
                match_score = match_max * min(vr / 2.0, 1.0) * 0.7

            # 突破放量比 (趋势市 0-5, 震荡市 0-6)
            breakout_max = 5 if is_trending else 6
            breakout_score = 0
            if len(bars) >= 20 and ind.get("ma20", 0) > 0:
                avg_vol_20 = sum(b["vol"] for b in bars[-20:]) / 20
                today_vol = bars[-1]["vol"] if bars else ind.get("vol", 0)
                close = bars[-1]["close"] if bars else ind.get("close", 0)
                ma20 = ind.get("ma20", 0)
                if close > ma20 and today_vol > 0 and avg_vol_20 > 0:
                    ratio = today_vol / avg_vol_20
                    if ratio > 2.0:
                        breakout_score = breakout_max
                    elif ratio > 1.5:
                        breakout_score = breakout_max * 0.7
                    elif ratio > 1.0:
                        breakout_score = breakout_max * 0.3

            # 缩量回调健康度 (趋势市 0-3, 震荡市 0-4)
            pullback_max = 3 if is_trending else 4
            pullback_score = 0
            if len(bars) >= 20 and ind.get("close", 0) > 0:
                avg_vol_20 = sum(b["vol"] for b in bars[-20:]) / 20
                today_vol = bars[-1]["vol"] if bars else ind.get("vol", 0)
                today_pct = bars[-1].get("pct_chg", 0) if bars else 0
                if today_pct < 0 and today_vol > 0 and avg_vol_20 > 0:
                    ratio = today_vol / avg_vol_20
                    if ratio < 0.7:
                        pullback_score = pullback_max
                    elif ratio < 1.0:
                        pullback_score = pullback_max * 0.5

            score = match_score + breakout_score + pullback_score
            scores[sym] = max(0, min(score, 15 if is_trending else 18))

        return scores

    def _compute_industry_relative_strength(
        self, candidates: List[Dict], quotes: Dict[str, dict],
        daily_bars: Dict[str, List[dict]], regime: str
    ) -> Dict[str, float]:
        """行业相对强度维度。"""
        scores: Dict[str, float] = {}
        is_trending = regime == REGIME_TRENDING

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
            sub_1d = 0
            if excess_1d > 2:
                sub_1d = 7
            elif excess_1d > 1:
                sub_1d = 5
            elif excess_1d > 0:
                sub_1d = 3
            elif excess_1d > -1:
                sub_1d = 1
            else:
                sub_1d = 0

            # 5日累计超额 (趋势市 0-6, 震荡市 0-7)
            sub_5d_max = 6 if is_trending else 7
            sub_5d = 0
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
                elif excess_5d > 2:
                    sub_5d = sub_5d_max * 0.6
                elif excess_5d > 0:
                    sub_5d = sub_5d_max * 0.3

            # 成交额贡献度 (趋势市 0-4, 震荡市 0-6)
            contrib_max = 4 if is_trending else 6
            sub_contrib = 0
            my_amount = q.get("amount", 0)
            total_amount = sum(quotes.get(p["ts_code"], {}).get("amount", 0) for p in peers)
            if total_amount > 0:
                share = my_amount / total_amount
                if share > 0.5:
                    sub_contrib = contrib_max
                elif share > 0.3:
                    sub_contrib = contrib_max * 0.7
                elif share > 0.2:
                    sub_contrib = contrib_max * 0.4

            score = sub_1d + sub_5d + sub_contrib
            scores[sym] = max(0, min(score, 17 if is_trending else 20))

        return scores

    def _compute_price_residual(
        self, candidates: List[Dict], quotes: Dict[str, dict],
        indicators: Dict[str, dict], regime: str
    ) -> Dict[str, float]:
        """价格残差维度（风险指标）。"""
        scores: Dict[str, float] = {}
        is_trending = regime == REGIME_TRENDING

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
            dev_score = 0
            if ma20 > 0 and close > 0:
                deviation = abs(close - ma20) / ma20 * 100
                dev_optimal_low = 3 if is_trending else 2
                dev_optimal_high = 10 if is_trending else 8
                if dev_optimal_low <= deviation <= dev_optimal_high:
                    dev_score = dev_max
                elif deviation < dev_optimal_low:
                    dev_score = dev_max * (deviation / dev_optimal_low)
                elif deviation < 15:
                    dev_score = dev_max * (15 - deviation) / (15 - dev_optimal_high)
                else:
                    dev_score = 0

            # 相对行业超额涨幅 (趋势市 0-6, 震荡市 0-7)
            excess_max = 6 if is_trending else 7
            # 用候选人所在行业的均值（由调用方提前算好）
            stock_pct = q.get("change_pct", 0)
            excess_score = 0
            if stock_pct > 2:
                excess_score = excess_max
            elif stock_pct > 1:
                excess_score = excess_max * 0.7
            elif stock_pct > 0:
                excess_score = excess_max * 0.4
            else:
                excess_score = 0

            # 非尾盘拉升验证 (0-3)
            tail_score = 3  # 默认满分（无尾盘拉升证据）
            open_p = q.get("open", 0)
            high_p = q.get("high", 0)
            pre_close = q.get("pre_close", 0)
            if close > pre_close and open_p > 0 and high_p > 0:
                total_gain = close - pre_close
                # 简单估算：如果收盘接近最高，可能尾盘拉升
                close_pct_of_range = (close - open_p) / max(high_p - open_p, 0.01)
                if close_pct_of_range > 0.9 and total_gain > 0:
                    tail_score = 1  # 疑似尾盘拉升

            score = dev_score + excess_score + tail_score
            scores[sym] = max(0, min(score, 15 if is_trending else 18))

        return scores

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
    ) -> Dict[str, float]:
        """资金持续性维度得分（仅 Top10）。"""
        scores: Dict[str, float] = {}
        is_trending = regime == REGIME_TRENDING

        for c in candidates:
            sym = c["ts_code"]
            mf = moneyflows.get(sym)

            if not mf:
                # 接口不可用，保持中性分
                scores[sym] = None
                continue

            # 当日主力净流入 / 流通市值归一化 (趋势市 0-10, 震荡市 0-8)
            main_abs_max = 10 if is_trending else 8
            main_net = mf.get("main_net", 0)
            market_cap = c.get("market_cap", 0) or 1
            flow_ratio = main_net / (market_cap * 1e8) if market_cap > 0 else 0  # market_cap is in 亿
            if flow_ratio > 0.003:
                main_score = main_abs_max
            elif flow_ratio > 0:
                main_score = main_abs_max * flow_ratio / 0.003
            else:
                main_score = 0

            # 主力净占比 (趋势市 0-8, 震荡市 0-7)
            pct_max = 8 if is_trending else 7
            main_pct = float(mf.get("main_pct", "0").replace("%", ""))
            if main_pct > 10:
                pct_score = pct_max
            elif main_pct > 0:
                pct_score = pct_max * main_pct / 10
            else:
                pct_score = 0

            # 5日累计主力净流入 / 流通市值 (0-7)
            d5_max = 7
            d5_main_net = mf.get("d5_main_net", 0)
            d5_ratio = d5_main_net / (market_cap * 1e8) if market_cap > 0 else 0
            if d5_ratio > 0.005:
                d5_score = d5_max
            elif d5_ratio > 0:
                d5_score = d5_max * d5_ratio / 0.005
            else:
                d5_score = 0

            score = main_score + pct_score + d5_score
            scores[sym] = max(0, min(score, 25 if is_trending else 22))

        return scores

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
