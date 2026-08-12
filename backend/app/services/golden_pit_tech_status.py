# -*- coding: utf-8 -*-
"""科技板块牛熊判断 + 现状数据服务。

按回测结论（data/backtest/_rotation_*）的口径输出：
  - 趋势腿: MA20 多头激活（close>MA20 且 MA20 5日斜率为正）→ 多/空/震荡
  - 贪婪腿: 各板块贪婪 250 日分位（超跌区 = 分位 <= 0.15）
数据源: 日K线走 tushare fund_daily（.env TUSHARE_TOKEN/TUSHARE_API_URL），
        tech7 贪婪走 arkvol tech-hardware-greed（TTL 2h），宽基贪婪走 DB 快照。
"""
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services.golden_pit_config import TECH_SECTOR_POOL

logger = logging.getLogger(__name__)

_cache: Dict[str, Any] = {}


def _cache_get(key: str, ttl: int) -> Any:
    item = _cache.get(key)
    if item and time.time() - item[0] < ttl:
        return item[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# 宽基（guide_only 择时指导）: 588000 科创50 / 159915 创业板指
BROAD_ITEMS = {
    "588000": {"name": "科创50", "etf_code": "SH588000", "tier": "broad"},
    "159915": {"name": "创业板指", "etf_code": "SZ159915", "tier": "broad"},
}

OVERSOLD_PCT = 0.15  # 贪婪 250 日分位 <= 0.15 视为超跌区


def _percentile(greed_series: Dict[str, float], lookback: int = 250) -> Optional[float]:
    """greed_series: {date: greed} → 最新值在过去 lookback 个观测中的分位。"""
    items = sorted((d, v) for d, v in greed_series.items() if v is not None)
    if not items:
        return None
    latest = items[-1][1]
    recent = [v for _, v in items[-lookback:]]
    if len(recent) < 20:
        return None
    return round(sum(1 for v in recent if v <= latest) / len(recent), 4)


def _load_broad_greed() -> Dict[str, Dict[str, float]]:
    """宽基贪婪历史（DB 快照 golden_pit_snapshot，经 get_history 读取）。"""
    from app.services.golden_pit_service import get_golden_pit_service
    out: Dict[str, Dict[str, float]] = {}
    try:
        svc = get_golden_pit_service()
        for code in BROAD_ITEMS:
            try:
                data = svc.get_history(index=code, days=500)
                series = (data or {}).get("series", {}).get(code, [])
                out[code] = {str(r["date"]): float(r["greed"]) for r in series if r.get("greed") is not None}
            except Exception as e:
                logger.warning("宽基贪婪历史加载失败 %s: %s", code, e)
    except Exception as e:
        logger.warning("宽基贪婪服务不可用: %s", e)
    return out


def _load_tech_greed() -> Dict[str, Dict[str, float]]:
    """tech7 贪婪历史（arkvol tech-hardware-greed/series，TTL 2h）。"""
    cached = _cache_get("tech_status_greed", 7200)
    if cached is not None:
        return cached
    from app.services.golden_pit_sector_service import _load_tech_greed_map
    out = _load_tech_greed_map()
    _cache_set("tech_status_greed", out)
    return out


def _kline(etf_code: str, limit: int = 140) -> List[Dict[str, Any]]:
    """ETF 日K线（tushare fund_daily，带 2h TTL）。"""
    from app.services.golden_pit_service import get_golden_pit_service
    key = f"tech_status_kline:{etf_code}"
    cached = _cache_get(key, 7200)
    if cached is not None:
        return cached
    try:
        bars = get_golden_pit_service()._cached_pi_kline(etf_code, limit=limit, ttl=7200)
    except Exception as e:
        logger.warning("ETF K线获取失败 %s: %s", etf_code, e)
        bars = []
    _cache_set(key, bars)
    return bars


def _trend_state(closes: List[float]) -> Dict[str, Any]:
    """MA20 趋势腿状态: 多=close>MA20且MA20 5日斜率为正; 空=close<MA20; 其余震荡。"""
    if len(closes) < 26:
        return {"trend": "数据不足", "ma20": None, "slope": None}
    ma20 = sum(closes[-20:]) / 20
    ma20_prev = sum(closes[-25:-5]) / 20
    slope = ma20 - ma20_prev
    last = closes[-1]
    if last > ma20 and slope > 0:
        trend = "多"
    elif last < ma20:
        trend = "空"
    else:
        trend = "震荡"
    return {"trend": trend, "ma20": round(ma20, 4), "slope": round(slope, 4)}


def _returns(closes: List[float]) -> Dict[str, float]:
    def chg(n: int) -> Optional[float]:
        if len(closes) <= n:
            return None
        return round(closes[-1] / closes[-1 - n] - 1, 4)
    hi60 = max(closes[-60:]) if len(closes) >= 60 else None
    return {
        "chg5": chg(5), "chg20": chg(20), "chg60": chg(60),
        "dd60": round(closes[-1] / hi60 - 1, 4) if hi60 else None,
    }


def _item_status(code: str, name: str, etf_code: str, greed_map: Dict[str, Dict[str, float]],
                 tier: str) -> Optional[Dict[str, Any]]:
    bars = _kline(etf_code)
    closes = [float(b["close"]) for b in bars if b.get("close") is not None]
    if len(closes) < 26:
        return None
    st = _trend_state(closes)
    ret = _returns(closes)
    series = greed_map.get(code, {})
    greed = None
    if series:
        try:
            greed = sorted((d, v) for d, v in series.items() if v is not None)[-1][1]
        except Exception:
            greed = None
    pct = _percentile(series)
    return {
        "code": code, "name": name, "etf_code": etf_code, "tier": tier,
        "close": round(closes[-1], 4), "trend": st["trend"], "ma20": st["ma20"],
        "ma20_slope": st["slope"], "greed": round(greed, 4) if greed is not None else None,
        "percentile": pct, "as_of": str(bars[-1]["date"]) if bars else None,
        **ret,
    }


def get_tech_status(as_of: Optional[str] = None) -> Dict[str, Any]:
    """主入口: 输出科技牛熊判断 + tech7/宽基现状。"""
    cache_key = f"tech_status:{as_of or datetime.now().strftime('%Y-%m-%d')}"
    cached = _cache_get(cache_key, 900)
    if cached is not None:
        return cached

    tech_greed = _load_tech_greed()
    broad_greed = _load_broad_greed()

    sectors = []
    for pool_key, entry in TECH_SECTOR_POOL.items():
        code = entry["etf_code"][2:]
        item = _item_status(code, f"{pool_key}·{entry['name']}", entry["etf_code"], tech_greed, "sector")
        if item:
            sectors.append(item)
    broad = []
    for code, entry in BROAD_ITEMS.items():
        item = _item_status(code, entry["name"], entry["etf_code"], broad_greed, "broad")
        if item:
            broad.append(item)

    all_items = sectors + broad
    trend_up = sum(1 for it in all_items if it["trend"] == "多")
    trend_down = sum(1 for it in all_items if it["trend"] == "空")
    pcts = [it["percentile"] for it in all_items if it.get("percentile") is not None]
    oversold = sum(1 for p in pcts if p <= OVERSOLD_PCT)
    avg_pct = round(sum(pcts) / len(pcts), 3) if pcts else None

    if trend_up >= 5:
        verdict, verdict_desc = "偏牛", "趋势腿过半激活，主升浪跟踪中"
    elif trend_up >= 3:
        verdict, verdict_desc = "震荡偏多", "部分板块站稳 MA20，趋势腿初步激活"
    elif trend_up >= 1:
        verdict, verdict_desc = "震荡筑底", "趋势腿尚未成势，个股板块分歧"
    elif oversold >= 5:
        verdict, verdict_desc = "熊市超跌反弹", "趋势腿空仓，贪婪腿超跌区可参与（反弹初期）"
    else:
        verdict, verdict_desc = "熊市弱势", "趋势与贪婪均未给信号，保持观察"

    summary = (
        f"科技现状: 趋势腿激活 {trend_up}/{len(all_items)}（空头 {trend_down}），"
        f"贪婪 250 日分位均值 {avg_pct if avg_pct is not None else '--'}（超跌区 {oversold} 只）。"
        f"判断: {verdict} — {verdict_desc}。"
    )

    _dates = [it["as_of"] for it in (sectors + broad) if it.get("as_of")]
    latest_as_of = max(_dates) if _dates else as_of
    result = {
        "as_of": latest_as_of,
        "verdict": verdict,
        "verdict_desc": verdict_desc,
        "trend_up_count": trend_up,
        "trend_down_count": trend_down,
        "total_count": len(all_items),
        "oversold_count": oversold,
        "avg_percentile": avg_pct,
        "oversold_pct_threshold": OVERSOLD_PCT,
        "broad": broad,
        "sectors": sectors,
        "summary": summary,
    }
    _cache_set(cache_key, result)
    return result
