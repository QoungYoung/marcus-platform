# -*- coding: utf-8 -*-
"""做T系统 · 选股层：可T质量打分 + 三层池（底仓候选池 / 做T实盘池 / 观察池）。

依据 final-t-plan.md §③ 与 spec t-account-trading：
- 可T质量三代理：可T价差空间（振幅中位−2×(滑点+手续费)>0 硬门槛）、O-C 回归度（|收-开|/振幅，≤0.45 加分/≥0.55 减分）、日内往返度（分钟线折返次数，1min 粒度）。
- 三层池流转：候选池(仅建仓) → 实盘池(已持仓+可T达标+过regime门+底仓≥下限，唯一可触发) → 观察池(缓冲)。
- 红线：禁止无底仓标的生成做T条件。
"""
import json
import math
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.database import SessionLocal
from app.services import t_db
from app.services.t_data_sources import fetch_minute_bars, fetch_tencent_quote

ACCOUNT_T = "t"

# 参数初值（P4 敏感度扫描后固化）
SLIPPAGE_TICKS = 2.5          # 双边滑点（最小报价单位），20-60 元中价股 2-5 tick
FEE_PCT = 0.001               # 手续费（万1 + 印花税近似）
MIN_T_SPREAD = 0.0            # 可T价差空间硬门槛（>0）
OC_LOW = 0.45                 # O-C 回归度 ≤0.45 加分（双向可T）
OC_HIGH = 0.55                # ≥0.55 减分（单边倾向）
TURNOVER_LOW = 1.0            # 换手率适中区间下限 %
TURNOVER_HIGH = 12.0          # 上限 %（高换手剔除，配合下跌市出货判断）
MIN_AMOUNT = 5e8              # 成交额下限（5 亿，元）
POOL_FLOOR_RATIO = 0.5        # 底仓保留下限：底仓市值 ≥ 持仓成本的 50%（P4 标定）


# ────────────────────────────────────────────────────────────────
# 可T质量打分
# ────────────────────────────────────────────────────────────────

def calc_t_quality(symbol: str, quote: Optional[dict] = None) -> Dict[str, Any]:
    """计算单标可T质量三代理 + 打分。

    Args:
        symbol: 股票代码（如 600519 / 000001.SZ）
        quote: 可选实时行情 dict（current/amount/turnover_rate/amplitude...），缺省时自动拉取
    Returns:
        {symbol, spread, oc_regression, round_trip, score, factors, pass_gate, reason}
    """
    if quote is None:
        from app.services.t_data_sources import _normalize_symbol
        q = fetch_tencent_quote([_normalize_symbol(symbol)])
        quote = q.get(_normalize_symbol(symbol)) or {}

    # 1) 可T价差空间：日内振幅中位数 − 2×(滑点+手续费)
    #    用近 6 日 m5 振幅中位数近似日内振幅（P0 探针②验证强分隔）
    bars = fetch_minute_bars(symbol, freq="m5", count=320)
    daily_amps = _calc_daily_amplitudes(bars) if bars else []
    if daily_amps:
        amp_median = _median(daily_amps)
    else:
        amp_median = float(quote.get("amplitude", 3.0) or 3.0)  # 降级用当日振幅
    price = float(quote.get("current", 0) or 0)
    tick = 0.01 if price and price >= 10 else 0.01
    slippage_cost = (SLIPPAGE_TICKS * tick * 2 + FEE_PCT * price) / price * 100 if price else 0.5
    spread = round(amp_median - 2 * slippage_cost, 3)

    # 2) O-C 回归度：|收-开| / 日内振幅（分钟线按日聚合）
    oc = _calc_oc_regression(bars) if bars else float(quote.get("amplitude", 0) or 0)

    # 3) 日内往返度：分钟线折返次数（1min 粒度；5min 粒度 P0 实测分隔弱）
    m1_bars = fetch_minute_bars(symbol, freq="m1", count=480)
    round_trip = _calc_round_trip(m1_bars) if m1_bars else _calc_round_trip(bars)

    # 4) 流动性
    amount = float(quote.get("amount", 0) or 0) * 10000  # 腾讯 amount 是万元
    turnover = float(quote.get("turnover_rate", 0) or 0)
    liq = _liquidity_score(amount, turnover)

    # 5) 打分（加权，P4 标定）
    spread_score = min(spread / 2.0, 1.0) if spread > 0 else 0.0
    oc_score = 1.0 if oc <= OC_LOW else (0.5 if oc <= OC_HIGH else 0.0)
    rt_score = min(round_trip / 20.0, 1.0) if round_trip > 0 else 0.0
    score = round(0.4 * spread_score + 0.3 * oc_score + 0.15 * rt_score + 0.15 * liq, 3)

    # 6) 硬门槛
    reasons = []
    if spread <= MIN_T_SPREAD:
        reasons.append("可T价差空间≤0（价差不覆盖成本）")
    if amount > 0 and amount < MIN_AMOUNT:
        reasons.append(f"成交额不足（{amount / 1e8:.1f}亿 < 5亿）")
    if turnover > 0 and (turnover < TURNOVER_LOW or turnover > TURNOVER_HIGH):
        reasons.append(f"换手率不适中（{turnover:.1f}%）")

    return {
        "symbol": symbol,
        "spread": spread,
        "oc_regression": round(oc, 3),
        "round_trip": round_trip,
        "score": score,
        "factors": {
            "amp_median": round(amp_median, 3) if daily_amps else None,
            "slippage_cost": round(slippage_cost, 4),
            "liquidity": liq,
            "amount": amount,
            "turnover": turnover,
        },
        "pass_gate": not reasons,
        "reasons": reasons,
    }


def _calc_daily_amplitudes(bars: List[dict]) -> List[float]:
    """按交易日聚合分钟线，计算每日振幅（(high-low)/pre_close 近似用 (high-low)/open）。"""
    by_day: Dict[str, List[dict]] = {}
    for b in bars:
        day = str(b["time"])[:10]
        by_day.setdefault(day, []).append(b)
    amps = []
    for day, day_bars in by_day.items():
        if len(day_bars) < 2:
            continue
        highs = [b["high"] for b in day_bars]
        lows = [b["low"] for b in day_bars]
        open_ = day_bars[0]["open"]
        if open_ <= 0:
            continue
        amps.append((max(highs) - min(lows)) / open_ * 100)
    return amps


def _calc_oc_regression(bars: List[dict]) -> float:
    """O-C 回归度：按日聚合 |收盘-开盘| / 日内振幅，取均值。"""
    by_day: Dict[str, List[dict]] = {}
    for b in bars:
        day = str(b["time"])[:10]
        by_day.setdefault(day, []).append(b)
    vals = []
    for day, day_bars in by_day.items():
        if len(day_bars) < 2:
            continue
        open_ = day_bars[0]["open"]
        close = day_bars[-1]["close"]
        high = max(b["high"] for b in day_bars)
        low = min(b["low"] for b in day_bars)
        rng = high - low
        if rng <= 0:
            continue
        vals.append(abs(close - open_) / rng)
    return _median(vals) if vals else 1.0


def _calc_round_trip(bars: List[dict]) -> int:
    """日内往返度：分钟线折返次数（连续同向若干根后反向计数）。"""
    if not bars or len(bars) < 3:
        return 0
    direction = 0  # 0 初始 / 1 上 / -1 下
    trips = 0
    prev_close = bars[0]["close"]
    for b in bars[1:]:
        close = b["close"]
        if close > prev_close:
            d = 1
        elif close < prev_close:
            d = -1
        else:
            d = 0
        if d != 0:
            if direction != 0 and d != direction:
                trips += 1
            direction = d
        prev_close = close
    return trips


def _liquidity_score(amount: float, turnover: float) -> float:
    """流动性打分：log成交额 + 换手率适中区间。"""
    score = 0.0
    if amount > 0:
        amt_yi = amount / 1e8
        score += min(max((math.log10(amt_yi + 1) - 1) / 2.0, 0.0), 1.0) * 0.7
    if turnover > 0:
        if TURNOVER_LOW <= turnover <= TURNOVER_HIGH:
            score += 0.3
        elif 0 < turnover < TURNOVER_LOW:
            score += 0.15
    return round(min(score, 1.0), 3)


def _median(vals: List[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


# ────────────────────────────────────────────────────────────────
# 三层池
# ────────────────────────────────────────────────────────────────

def _get_positions() -> List[Dict[str, Any]]:
    """读取 t 账户当前持仓（paper_positions）。"""
    try:
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT symbol, volume, frozen, avg_price FROM paper_positions "
                "WHERE account_id = 't' AND volume > 0"
            )).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        print(f"[t-pool] 读取持仓失败: {e}")
        return []


def _get_holding_floor(symbol: str, positions: List[dict]) -> float:
    """底仓保留下限：当前持仓市值 × POOL_FLOOR_RATIO（元）。"""
    for p in positions:
        if p["symbol"] == symbol:
            return float(p["volume"] or 0) * float(p["avg_price"] or 0) * POOL_FLOOR_RATIO
    return 0.0


def compute_three_tier_pool(regime: str = "ACTIVE") -> Dict[str, Dict[str, Any]]:
    """计算三层池并返回 {tier: {symbol: {...}}}。

    - 底仓候选池：未持仓但可T打分达标的标的（来自候选池/用户关注池，本实现从持仓外标的评估）
    - 做T实盘池：已持仓 + 可T达标 + 过 regime 门 + 底仓≥下限
    - 观察池：已持仓但暂不满足条件的
    """
    positions = _get_positions()
    held_symbols = {p["symbol"] for p in positions}

    # 做T实盘池：从持仓中筛选
    live_pool: Dict[str, Dict[str, Any]] = {}
    watch_pool: Dict[str, Dict[str, Any]] = {}
    for pos in positions:
        symbol = pos["symbol"]
        q = calc_t_quality(symbol)
        floor = _get_holding_floor(symbol, positions)
        pos_value = float(pos["volume"] or 0) * float(pos["avg_price"] or 0)
        meets_regime = regime in ("ACTIVE", "CAUTIOUS")
        meets_floor = pos_value >= floor
        if q["pass_gate"] and meets_regime and meets_floor:
            live_pool[symbol] = {
                "symbol": symbol,
                "score": q["score"],
                "spread": q["spread"],
                "oc": q["oc_regression"],
                "round_trip": q["round_trip"],
                "volume": pos["volume"],
                "avg_price": pos["avg_price"],
                "tier": "live",
            }
        else:
            watch_pool[symbol] = {
                "symbol": symbol,
                "score": q["score"],
                "reason": q["reasons"] or (["regime门未过"] if not meets_regime else ["底仓不足"]),
                "tier": "watch",
            }

    # 底仓候选池：从历史关注/候选池中未持仓标的评估（示例：取候选池 JSON 前 N 只）
    candidate_pool = _load_candidate_symbols()
    cand_pool: Dict[str, Dict[str, Any]] = {}
    for symbol in candidate_pool:
        if symbol in held_symbols:
            continue
        q = calc_t_quality(symbol)
        if q["pass_gate"] and q["score"] >= 0.5:
            cand_pool[symbol] = {
                "symbol": symbol,
                "score": q["score"],
                "spread": q["spread"],
                "oc": q["oc_regression"],
                "tier": "candidate",
            }

    return {"candidate": cand_pool, "live": live_pool, "watch": watch_pool}


def _load_candidate_symbols() -> List[str]:
    """候选池来源：candidate_pool.json（复用现有候选池，仅取前 20 只评估）。"""
    try:
        from app.services.candidate_pool import get_candidate_pool
        pool = get_candidate_pool()
        symbols = []
        for item in pool.pool[:20]:
            sym = item.get("symbol") if isinstance(item, dict) else str(item)
            if sym:
                symbols.append(sym)
        return symbols
    except Exception as e:
        print(f"[t-pool] 候选池读取失败: {e}")
        return []


def generate_conditions_for_live_pool(regime: str = "ACTIVE") -> List[Dict[str, Any]]:
    """为做T实盘池标的生成 t_conditions 条件元组（仅 account_id='t' 且有底仓标的）。

    条件：低吸触发价 = 支撑位（前低/均价*0.98 近似），复归价 = 触发价*(1+0.4%)，
    量比阈值按可T质量默认，企稳确认 stabilize_level='not_new_low'，卖出目标/止损随条件写入。
    """
    pool = compute_three_tier_pool(regime=regime)
    created = []
    for symbol, info in pool["live"].items():
        avg_price = float(info.get("avg_price", 0) or 0)
        if avg_price <= 0:
            continue
        target = round(avg_price * 0.98, 2)          # 低吸触发价：成本价 -2%（支撑位近似）
        reinform = round(target * 1.004, 2)          # 复归价：触发价 +0.4%
        sell_target = round(avg_price * 1.015, 2)    # 高抛目标：成本价 +1.5%
        stop_loss = round(target * 0.97, 2)          # 止损：触发价 -3%
        cond = {
            "account_id": ACCOUNT_T,
            "symbol": symbol,
            "trigger_kind": "low_buy",
            "target_price": target,
            "reinform_price": reinform,
            "vol_ratio_thresh": 1.5,
            "stabilize_level": "not_new_low",
            "sell_target_price": sell_target,
            "stop_loss_price": stop_loss,
            "time_stop_close": "14:45",
            "start_time": "09:30",
            "end_time": "14:45",
            "regime_gate": "ALLOWED",
            "status": "active",
            "armed": 1,
        }
        cid = t_db.upsert_condition(cond)
        if cid:
            created.append({"condition_id": cid, **cond})
    return created
