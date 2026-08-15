# -*- coding: utf-8 -*-
"""做T系统 · market_regime 环境闸门（三层合成单一总开关）。

依据 final-t-plan.md §⑤ 与 spec t-regime-gate：
- L1 日频基准层：复用 market_diagnosis（state/score_trend/score_oscillation/score_extreme）+ 指数日线 MA20/60
- L2 日内动态前哨：腾讯 qt 指数实时跌幅/放量破5日均线（分钟级）
- L3 硬保险丝：沪深300当日跌>2% → 无条件 HALT
- 合成输出三态 ACTIVE/CAUTIOUS/HALT + 量能解读符号；写入 t_regime_state
- TMonitor 写 t_triggers 前先过 GATE（BLOCKED 不写 / MANUAL_ONLY 挂人）
"""
from datetime import datetime
from typing import Dict, Optional

from app.services import t_db
from app.services.t_data_sources import fetch_tencent_quote

# 硬保险丝阈值（沪深300 当日跌幅 > 2% → HALT，P4 标定）
HARD_FUSE_DROP = 2.0
# 日内动态前哨：指数实时跌幅 > 0.8% 即 WARN → CAUTIOUS（初跌领先预警）
INTRADAY_WARN_DROP = 0.8
# 指数代码（腾讯格式）
INDEX_SYMBOLS = {"hs300": "sh000300", "sh": "sh000001", "sz": "sz399001"}

# 缓存：避免同一轮重复拉取（5s）
_regime_cache: Dict[str, object] = {"ts": 0, "result": None}
_CACHE_TTL = 5.0


def _is_trading_time(now: Optional[datetime] = None) -> bool:
    """A 股交易时段门控（9:30-11:30 / 13:00-15:00，周一至周五）。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    hm = now.hour * 100 + now.minute
    return (930 <= hm <= 1130) or (1300 <= hm <= 1500)


def _read_market_diagnosis() -> Optional[dict]:
    """读 market_diagnosis 当日 state（复用现成数据，不重造）。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        today = datetime.now().strftime("%Y%m%d")
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT state, score_trend, score_oscillation, score_extreme "
                "FROM market_diagnosis WHERE trade_date = :td"
            ), {"td": today}).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-regime] market_diagnosis 读取失败: {e}")
        return None


def _fetch_index_quotes() -> Dict[str, Optional[dict]]:
    """拉取指数实时行情（沪深300/上证/深成指）。"""
    return fetch_tencent_quote(list(INDEX_SYMBOLS.values()))


def compose_regime(day_grade: str, intraday_warn: bool, hs300_drop: float) -> Dict[str, str]:
    """纯合成：L1 日频基准 + L2 日内前哨 + L3 硬保险丝 → 三态档位 + 闸门 + 量能解读符号。

    实时路径（compute_regime）与回测路径共用，保证判定规则单一来源。
    Args:
        day_grade: L1 日频基准归一档（HALT/CAUTIOUS/ACTIVE，来自 market_diagnosis 或历史近似）。
        intraday_warn: L2 日内前哨（任一指数实时跌幅 ≤ -0.8%）。
        hs300_drop: L3 硬保险丝输入（沪深300 当日跌幅 %）。
    """
    hard_fuse = hs300_drop <= -HARD_FUSE_DROP
    if hard_fuse:
        regime = "HALT"
    elif day_grade == "HALT":
        regime = "HALT"
    elif intraday_warn:
        regime = "CAUTIOUS"
    else:
        regime = day_grade
    if regime == "HALT":
        gate_low_buy, gate_high_sell = "BLOCKED", "ALLOWED"
        interpret_sign = -1
    elif regime == "CAUTIOUS":
        gate_low_buy, gate_high_sell = "MANUAL_ONLY", "ALLOWED"
        interpret_sign = 0
    else:  # ACTIVE
        gate_low_buy, gate_high_sell = "ALLOWED", "ALLOWED"
        interpret_sign = 1
    return {
        "regime": regime,
        "gate_low_buy": gate_low_buy,
        "gate_high_sell": gate_high_sell,
        "interpret_sign": interpret_sign,
        "hard_fuse": hard_fuse,
    }


def compute_regime(force: bool = False) -> Dict[str, str]:
    """计算并落库当日环境闸门状态。返回 {regime, gate_low_buy, gate_high_sell, interpret_sign, ...}。

    合成规则：
        若 硬保险丝(沪深300跌>2%) → HALT
        若 regime_day = HALT → HALT
        若 regime_intraday = WARN → CAUTIOUS（下限禁用低吸）
        否则 → regime_day
    """
    now = datetime.now()
    if not force and _regime_cache["result"] and (now.timestamp() - _regime_cache["ts"]) < _CACHE_TTL:
        return _regime_cache["result"]

    # ── L3 硬保险丝：沪深300 实时跌幅 ──
    quotes = _fetch_index_quotes()
    hs300 = quotes.get(INDEX_SYMBOLS["hs300"]) or {}
    index_drop = float(hs300.get("change_pct", 0) or 0)
    hard_fuse = index_drop <= -HARD_FUSE_DROP

    # ── L1 日频基准 ──
    diag = _read_market_diagnosis()
    state = (diag or {}).get("state", "trend")
    # market_diagnosis state 语义：trend/oscillation/extreme 等，归一为 regime_day
    if state in ("extreme", "risk", "bear"):
        regime_day = "HALT"
    elif state in ("trend_up", "up"):
        regime_day = "CAUTIOUS"
    elif state in ("oscillation", "range", "trend"):
        # 默认 trend 视为震荡可做T（做T 只在震荡市成立），但叠加日内前哨
        regime_day = "ACTIVE"
    else:
        regime_day = "ACTIVE"

    # ── L2 日内动态前哨：指数实时跌幅/情绪 ──
    warn = False
    if _is_trading_time(now):
        for sym, q in quotes.items():
            if q and float(q.get("change_pct", 0) or 0) <= -INTRADAY_WARN_DROP:
                warn = True
                break

    # ── 合成（纯函数，回测共用）──
    composed = compose_regime(regime_day, warn, index_drop)
    regime = composed["regime"]
    gate_low_buy = composed["gate_low_buy"]
    gate_high_sell = composed["gate_high_sell"]
    interpret_sign = composed["interpret_sign"]

    result = {
        "regime": regime,
        "regime_day": regime_day,
        "intraday_warn": warn,
        "hard_fuse": composed["hard_fuse"],
        "index_drop": round(index_drop, 2),
        "gate_low_buy": gate_low_buy,
        "gate_high_sell": gate_high_sell,
        "interpret_sign": interpret_sign,
        "daily_source": diag.get("state") if diag else "default",
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
    }

    # 落库
    t_db.upsert_regime_state(result)
    _regime_cache.update({"ts": now.timestamp(), "result": result})
    return result


def check_gate(trigger_kind: str = "low_buy", regime_state: Optional[dict] = None) -> Dict[str, str]:
    """TMonitor 前置 GATE：判断某触发类型当前是否允许。

    Returns: {"allowed": bool, "mode": "auto"|"human_confirm"|"blocked", "regime": str}
    """
    st = regime_state or compute_regime()
    if trigger_kind in ("high_sell", "high_sell_then_buy_back"):
        gate = st.get("gate_high_sell", "ALLOWED")
    else:  # low_buy 及默认
        gate = st.get("gate_low_buy", "ALLOWED")
    if gate == "BLOCKED":
        return {"allowed": False, "mode": "blocked", "regime": st.get("regime", "ACTIVE")}
    if gate == "MANUAL_ONLY":
        return {"allowed": True, "mode": "human_confirm", "regime": st.get("regime", "ACTIVE")}
    return {"allowed": True, "mode": "auto", "regime": st.get("regime", "ACTIVE")}
