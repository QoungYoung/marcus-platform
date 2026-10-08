# -*- coding: utf-8 -*-
"""做T系统 · market_regime 环境闸门（三层合成单一总开关）。

依据 final-t-plan.md §⑤ 与 spec t-regime-gate：
- L1 日频基准层：复用 market_diagnosis（state/score_trend/score_oscillation/score_extreme）+ 指数日线 MA20/60
- L2 日内动态前哨：腾讯 qt 指数实时跌幅/放量破5日均线（分钟级）
- L3 硬保险丝：沪深300当日跌>2% → 无条件 HALT
- 合成输出三态 ACTIVE/CAUTIOUS/HALT + 量能解读符号；写入 t_regime_state
- TMonitor 写 t_triggers 前先过 GATE（BLOCKED 不写 / MANUAL_ONLY 挂人）
"""
import os
import time
from datetime import datetime
from typing import Any, Dict, Optional

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


# ── 交易日日历（2026-10-08 修，用户拍板「修」）──────────────────────────────────
#   原实现只看 `weekday() >= 5` ✗ ⇒ **节假日照跑**：实测 2026-09-25（中秋）与 10-01~10-07（国庆）
#   TMonitor 仍在评估并写 t_triggers（09-25 共 432 行、假期每天约 470 行 ✓ 状态全为 blocked/info ✓
#   未造成成交 ✓ —— 但白跑 + 写库噪声 ✗）。这里加一层日历门 ✓。
#   ★ 取不到日历时**退回 weekday 规则**（fail-open ⇒ 与旧行为一致 ✓ 不会因日历故障停掉交易 ✓）。
_CAL_CACHE: Dict[str, Optional[bool]] = {"day": "", "is_open": None}

# ── 指数报价时间戳缓存（2026-10-08 用户要求「加上缓存」）──────────────────────────
#   原来每轮（TMonitor 约 30s）都发一次 `https://qt.gtimg.cn/q=sh000001`（该函数**无缓存**）
#   ⇒ 09:40~15:00 约 2 次/分钟 ✗。现在默认 **300s** 查一次 ✓（`WOLF_CAL_QUOTE_TTL` 可调；≤0 = 不缓存）。
#   ★ **非对称 TTL**：判「开市」用完整 TTL；判「休市」最多缓存 60s ✓
#     （万一开盘那一刻报价尚未刷新而误判休市 ⇒ 只跳 1~2 轮而不是 5 分钟 ✓）
#   ★ 判据不缓存"失败"：取不到 ⇒ 返回 None（当轮交给日历/星期兜底 ✓ 不留脏缓存 ✓）
_QUOTE_CACHE: Dict[str, Any] = {"day": "", "at": 0.0, "dt": "", "verdict": False}


def _quote_dt(now: Optional[datetime] = None) -> tuple:
    """指数（上证 sh000001）报价时间戳的日期部分（YYYYMMDD），带缓存。

    返回 `(dq, fresh)`：`dq` 取不到 ⇒ `(None, False)`；`fresh=True` 表示**本次真的发了请求**
      （缓存命中 ⇒ False ✓ —— 连续确认只数新鲜观测，不数缓存命中 ✓）。
    """
    now = now or datetime.now()
    d8 = now.strftime("%Y%m%d")
    try:
        ttl = float(os.getenv("WOLF_CAL_QUOTE_TTL", "300") or 300)
    except Exception:
        ttl = 300.0
    c = _QUOTE_CACHE
    if ttl > 0 and c.get("day") == d8 and c.get("dt"):
        _age = time.monotonic() - float(c.get("at") or 0.0)
        # ★ 判「开市」用完整 TTL(默认 300s)；判「休市」只缓存 20s ⇒ 连续确认能在 ~1 分钟内完成 ✓
        _lim = ttl if c.get("verdict") else min(ttl, 20.0)
        if _age < _lim:
            return str(c["dt"]), False
    try:
        from app.services.t_data_sources import fetch_tencent_quote
        q = (fetch_tencent_quote(["sh000001"]) or {}).get("sh000001") or {}
        _dq = str(q.get("quote_dt") or "")[:8]
    except Exception as _e:
        print("[t_regime] 行情时间戳判据失败(继续用日历/星期): %s" % str(_e)[:70], flush=True)
        return None, False
    if not (len(_dq) == 8 and _dq.isdigit()):
        return None, False
    c.update({"day": d8, "at": time.monotonic(), "dt": _dq, "verdict": (_dq == d8)})
    return _dq, True


# ── 「疑似休市」连续确认 + 每日一次告警（2026-10-08 用户要求三条都做）───────────
#   为什么要确认：「休市」是**否决整场**的结论 ⇒ 单次观测就下结论风险不对称 ✗
#   为什么缓存命中不计数：否则 5 分钟一次缓存也会在 3 轮内凑满"确认次数"（假确认 ✗）
_CLOSED: Dict[str, Any] = {"day": "", "n": 0, "alerted": False}


def _closed_confirm_n() -> int:
    """确认所需**新鲜观测**次数（`WOLF_CAL_CLOSED_CONFIRM`，默认 3）"""
    try:
        return max(1, int(os.getenv("WOLF_CAL_CLOSED_CONFIRM", "3") or 3))
    except Exception:
        return 3


def _closed_seen(d8: str, fresh: bool) -> int:
    c = _CLOSED
    if c.get("day") != d8:
        c.update({"day": d8, "n": 0, "alerted": False})
    if fresh:
        c["n"] = int(c.get("n") or 0) + 1
    return int(c.get("n") or 0)


def _closed_clear(d8: str) -> None:
    """见到「开市」⇒ 立刻清零（不残留昨天的计数 ✓）"""
    if _CLOSED.get("day") != d8 or _CLOSED.get("n"):
        _CLOSED.update({"day": d8, "n": 0})


def _closed_alert(d8: str, dq: str) -> None:
    """判休市 ⇒ **每天一次** QQ 告警（`WOLF_CAL_ALERT=0` 可关）✓"""
    if _CLOSED.get("alerted"):
        return
    if str(os.getenv("WOLF_CAL_ALERT", "1")).strip().lower() in ("0", "false", "no", "off"):
        _CLOSED["alerted"] = True
        return
    _CLOSED["alerted"] = True
    msg = ("[交易日门] 指数报价时间戳=%s ≠ 今天 %s ⇒ 判为**休市**（节假日/停市）："
           "TMonitor 今日不评估、不发腿（已连续 %d 次新鲜观测确认）" % (dq, d8, _closed_confirm_n()))
    print("[t_regime] ★ " + msg, flush=True)
    try:
        from app.services import alert_hub as _ah
        _ah.push_qq(msg)
    except Exception as _e:
        print("[t_regime] 休市告警推送失败: %s" % str(_e)[:80], flush=True)


def _is_trading_day(now: Optional[datetime] = None) -> bool:
    """今天是不是**交易日**（含节假日；2026-10-08 修，用户拍板「修」）。

    判据优先级（每层都有 fail-open 兜底 ⇒ **不会因数据源故障停掉交易** ✓）：
      ① **行情自身**（最可靠、离线 ✓）：指数报价**时间戳**的日期 ≠ 今天 ⇒ **休市** ✓
         实测：节假日腾讯报价冻结在**上一交易日**（10-01~10-07 价格恒为 56.900 = 09-30 收盘 ✓）
         ⚠️ **09:40 前不采信**（开盘前报价仍带上一日时间戳 ✗）；取不到报价 ⇒ 进 ②
      ② **交易日历**（`mkt_bars.trade_days`）：★ 生产实测该源**当前不可靠** ——
         `trade_cal` 报 `tenant key expired` / 连发触发限流后**降级成"周一~周五全算交易日"** ✗
         ⇒ **只采信它的"休市"结论** ✓（"开市"不采信，交给 ①③）
      ③ 退回 `weekday() < 5`（**旧行为** ✓ 零回归）
    """
    now = now or datetime.now()
    d8 = now.strftime("%Y%m%d")
    hm = now.hour * 100 + now.minute
    # ① 行情时间戳（**09:30 起**采信；带缓存 + 连续确认 + 每日一次告警）
    #   为什么 09:30 而不是 09:40：实测交易日 **09:31:05** 的第一条触发已带当日价（SZ002156 57.193
    #   vs 节前收盘 56.9 ✓）⇒ 集合竞价后报价即为当日 ✓；收紧后假期 09:30–09:40 的约 20 轮空跑也省掉 ✓
    if hm >= 930:
        _dq, _fresh = _quote_dt(now)
        if _dq:
            if _dq == d8:
                _closed_clear(d8)
                return True
            if _closed_seen(d8, _fresh) >= _closed_confirm_n():
                _closed_alert(d8, _dq)
                return False
            return now.weekday() < 5      # 确认期内**先按旧行为**（不跳 ✓ 避免单次抖动误判）
    # ② 交易日历（每天只查一次；只采信"休市"）
    if _CAL_CACHE.get("day") != d8:
        _CAL_CACHE["day"] = d8
        _CAL_CACHE["is_open"] = None
        try:
            from app.services.mkt_bars import trade_days      # 与 sync_mkt_bars 同一日历源 ✓
            _CAL_CACHE["is_open"] = d8 in set(trade_days(d8, d8) or [])
        except Exception as _e:
            _CAL_CACHE["is_open"] = None
            print("[t_regime] 交易日历取数失败 ⇒ 退回 weekday 判定: %s" % str(_e)[:80], flush=True)
    if _CAL_CACHE.get("is_open") is False:
        return False
    # ③ 星期兜底（旧行为）
    return now.weekday() < 5


def _is_trading_time(now: Optional[datetime] = None) -> bool:
    """A 股交易时段门控（**交易日** ∧ 9:30-11:30 / 13:00-15:00）。"""
    now = now or datetime.now()
    if not _is_trading_day(now):
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
