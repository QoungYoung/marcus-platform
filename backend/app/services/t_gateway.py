# -*- coding: utf-8 -*-
"""做T系统 · 执行风控网关（唯一放行者）+ 当日可卖额度账本 + 熔断。

依据 final-t-plan.md §⑥ 与 spec t-execution-risk：
- 三权分立：Worker 事件发生器 / Agent 复核决策者 / 网关唯一放行者（本模块 = 网关）
- 网关三阶校验：硬闸门(裸空/跌停/STOP_ALL/白名单 O(1)) → 账本(可卖底仓断言/买腿≤可卖底仓/日亏回转额熔断) → 建议层(单笔%/冷却/价差成本比/频次护栏仅告警)
- 二段实时断言：落单前重拉最新持仓/价格/跌停/熔断
- 当日可卖额度原子账本：卖腿扣减(UPDATE...RETURNING)、买腿回补、卖出在途锁
- 可卖底仓分档 L0-L3；异常升级 6 类清单；STOP_ALL/日亏熔断；孤儿单处置；滑点/价差过滤
"""
from datetime import datetime, timedelta
import os
from typing import Any, Dict, Optional, Tuple

from sqlalchemy import text

from app.database import SessionLocal
from app.services import t_db
from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote
from app.services.t_regime import compute_regime

ACCOUNT_T = "t"

# 底仓风控开关（灰度用；默认开）
T_STOP_GUARD_ENABLED = os.getenv("T_STOP_GUARD_ENABLED", "1") != "0"
BASE_LOSS_HALF_PCT = 3.0      # 底仓浮亏 −3% 减半仓
BASE_LOSS_CLEAR_PCT = 5.0     # 底仓浮亏 −5% 清仓+当日锁定
MAX_DAILY_BUY_LEGS = 2        # 单标单日低吸（买腿成交）次数上限
# 买腿分档上限开关（L1 档买腿≤可卖底仓×0.5）——关闭后买腿≤可卖底仓全额（AI 自由跑用）
T_BUY_TIER_LIMIT_ENABLED = os.getenv("T_BUY_TIER_LIMIT_ENABLED", "1") != "0"
# 日回转额上限开关（日累计回转额≤3×净值）——关闭后不做回转额拦截（AI 自由跑用）
T_TURNOVER_LIMIT_ENABLED = os.getenv("T_TURNOVER_LIMIT_ENABLED", "1") != "0"

# ── 参数（P4 敏感度扫描标定，当前保守档初值） ──
MAX_SINGLE_ORDER_PCT = 0.05        # 单笔 ≤ 净值 5%（建议层）
DAILY_LOSS_BREAKER_PCT = 0.02      # 日亏 2% 熔断（硬闸门）
DAILY_LOSS_WARN_PCT = 0.01         # 日亏 1% 预警（建议层）
MAX_SELL_FLOOR_RATIO = 1.0         # 买腿 ≤ 可卖底仓（L2 默认 1:1）
COOLDOWN_AFTER_LOSS_MIN = 15       # 亏损后冷却（标准档 15min）
SLIPPAGE_PCT = 0.001               # 滑点参数化假设 0.1%（约 2-5 tick 中价股）
COST_RATIO_LIMIT = 0.2             # 滑点+手续费 > 价差空间 20% 不触发
MIN_T_SPREAD_FILTER = 0.002        # 最低价差过滤（相对价 0.2%）
MAX_DAILY_TURNOVER_RATIO = 3.0     # 日累计回转额 ≤ 3×净值（主指标）
FLOOR_LOWER_RATIO = 0.5            # 底仓保留下限（市值 ≥ 成本 50%）
TRIGGER_EXEC_TIMEOUT_MIN = 2       # human_confirm 超时 2min → cancelled

# 可卖底仓分档
TIER_L0 = "L0"  # 禁用低吸（下跌市/近跌停）
TIER_L1 = "L1"  # 买腿 ≤ 可卖底仓×0.5
TIER_L2 = "L2"  # 买腿 ≤ 可卖底仓×1.0（默认）
TIER_L3 = "L3"  # 买腿 ≤ 可卖底仓×1.5 + 日回转额上限


# ────────────────────────────────────────────────────────────────
# t 账户净值（统一基准，替换散落的 initial=200000 硬编码）
# ────────────────────────────────────────────────────────────────

def t_net_asset() -> float:
    """读取 t 账户当前净值 = 可用资金 + 冻结资金 + 持仓市值（以 paper_account_info 为准）。

    替代历史硬编码 initial=200000（t_gateway.py 旧 _daily_pnl_pct / 日回转额上限），
    调额（POST /t/account/capital-adjust）后自动反映新值。
    读取失败时回退注册资金（paper_accounts.initial_capital），再退 200000 保守值。
    """
    try:
        db = SessionLocal()
        try:
            acct = db.execute(text(
                "SELECT available_cash, frozen_cash FROM paper_account_info WHERE account_id = 't'"
            )).mappings().first()
            positions = db.execute(text(
                "SELECT volume, avg_price FROM paper_positions WHERE account_id = 't' AND volume > 0"
            )).mappings().all()
            if acct is None:
                reg = db.execute(text(
                    "SELECT initial_capital FROM paper_accounts WHERE account_id = 't'"
                )).mappings().first()
                return float(reg["initial_capital"] or 200000) if reg else 200000.0
            available = float(acct.get("available_cash") or 0)
            frozen = float(acct.get("frozen_cash") or 0)
            pos_value = sum(float(p["volume"] or 0) * float(p["avg_price"] or 0) for p in positions)
            total = available + frozen + pos_value
            return round(total, 2) if total > 0 else 200000.0
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] t_net_asset 读取失败: {e}")
        return 200000.0


# ────────────────────────────────────────────────────────────────
# 账本（当日可卖额度）
# ────────────────────────────────────────────────────────────────

def get_sellable_ledger() -> Dict[str, Dict[str, Any]]:
    """读取 t 账户"当日可卖额度"账本（基于 paper_positions + 当日成交推算）。

    可卖额度 = 持仓（昨日及以前净买入，T+1 可卖）− 今日已卖 + 今日买回成交回补。
    简化实现：以 paper_positions 当日持仓为基数，扣除今日买入（当日不可卖）得到可卖部分。
    """
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            # 今日买入量（T+1 锁定，不可卖）
            buys = db.execute(text(
                "SELECT symbol, COALESCE(SUM(volume), 0) AS v FROM paper_trades "
                "WHERE account_id = 't' AND direction = '买入' AND voided = 0 "
                "AND substr(created_at, 1, 10) = :today GROUP BY symbol"
            ), {"today": today}).mappings().all()
            buy_map = {r["symbol"]: int(r["v"]) for r in buys}
            # 持仓
            pos = db.execute(text(
                "SELECT symbol, volume, frozen, avg_price FROM paper_positions "
                "WHERE account_id = 't' AND volume > 0"
            )).mappings().all()
            ledger = {}
            for p in pos:
                symbol = p["symbol"]
                volume = int(p["volume"] or 0)
                today_buy = buy_map.get(symbol, 0)
                # 可卖 = 持仓 − 今日买入（当日买回部分 T+1 锁定）
                sellable = max(volume - today_buy, 0)
                ledger[symbol] = {
                    "symbol": symbol,
                    "volume": volume,
                    "today_buy": today_buy,
                    "sellable": sellable,
                    "avg_price": float(p["avg_price"] or 0),
                }
            return ledger
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 读取可卖账本失败: {e}")
        return {}


def _atomic_decrement_sellable(symbol: str, qty: int) -> bool:
    """卖腿下单原子扣减可卖额度（当前以 paper_positions 为基础，由引擎撮合天然保证 T+1）。

    说明：模拟盘撮合（PaperTradingEngine）在卖出时校验持仓与 T+1 规则；
    此处提供账本级断言（可卖≥下单量），与引擎校验双保险。
    """
    ledger = get_sellable_ledger()
    item = ledger.get(symbol)
    if not item:
        return False
    return item["sellable"] >= qty


def is_sell_in_transit(symbol: str) -> bool:
    """卖出在途锁定：当日有未确认成交的卖单则该标的锁买腿。"""
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            row = db.execute(text(
                "SELECT 1 FROM paper_orders WHERE account_id = 't' AND symbol = :symbol "
                "AND direction = '卖出' AND status IN ('提交中', '部分成交') "
                "AND substr(created_at, 1, 10) = :today LIMIT 1"
            ), {"symbol": symbol, "today": today}).fetchone()
            return row is not None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 卖出在途查询失败: {e}")
        return False


# ────────────────────────────────────────────────────────────────
# 熔断 / 状态
# ────────────────────────────────────────────────────────────────

def check_breakers() -> Tuple[bool, str]:
    """日亏损熔断 + STOP_ALL + 连续亏损 → 返回 (触发, 原因)。"""
    risk = t_db.get_risk_state() or {}
    if risk.get("stop_all"):
        return True, "STOP_ALL 已触发"
    if risk.get("manual_lock"):
        return True, f"人工锁定: {risk.get('lock_reason') or 'manual'}"
    daily = t_db.get_daily_state() or {}
    if daily.get("risk_breaker"):
        return True, daily.get("breaker_reason") or "日亏损熔断"
    # 连续亏损
    if int(risk.get("consecutive_losses") or 0) >= 3:
        return True, "连续亏损 ≥ 3 次，临时禁自动"
    return False, ""


def _daily_pnl_pct() -> float:
    """当日已实现盈亏 / 初始资金 百分比（近似，基准用 t 账户当前净值）。"""
    daily = t_db.get_daily_state() or {}
    initial = t_net_asset()
    realized = float(daily.get("realized_pnl") or 0)
    return realized / initial * 100 if initial else 0.0


# ────────────────────────────────────────────────────────────────
# 可卖底仓分档
# ────────────────────────────────────────────────────────────────

def _floor_tier(regime: str, near_limit_down: bool) -> str:
    if near_limit_down or regime == "HALT":
        return TIER_L0
    if regime == "CAUTIOUS":
        return TIER_L1
    return TIER_L2


def _max_buy_volume(symbol: str, tier: str, ledger: Optional[dict] = None,
                    price: Optional[float] = None,
                    condition_id: Optional[int] = None) -> int:
    """按分档计算买腿上限（股数）。ledger 可注入（回测账本），默认实时账本。

    T_BUY_TIER_LIMIT_ENABLED=0（AI 自由跑）：不设档位上限——卖出后允许重新建仓再 T，
    买腿数量由单笔额度/总仓位约束（建议层）兜底，返回极大值放行。
    迭代#58：无底仓时仅条件单触发路径（condition_id 提供）放行，上限 = 建仓规模
    建议股数（单笔上限÷现价，含单标/总底仓上限校验）；否则返回 0（禁买）。
    """
    if ledger is None:
        ledger = get_sellable_ledger()
    item = ledger.get(symbol)
    sellable = item["sellable"] if item else 0
    if not T_BUY_TIER_LIMIT_ENABLED:
        return max(sellable, 10**9)  # 自由跑：不限量（上限由单笔/总仓位约束）
    if tier == TIER_L0:
        return 0
    if sellable <= 0:
        # 无底仓：条件单建仓（买腿=开仓），上限取建仓规模建议股数
        if condition_id and price and price > 0:
            try:
                from app.services.t_build import build_sizing
                sz = build_sizing(symbol, price)
                if sz.get("pass"):
                    return int(sz.get("suggest_volume") or 0)
            except Exception:
                pass
        return 0
    if tier == TIER_L1:
        return int(sellable * 0.5)
    if tier == TIER_L2:
        return sellable
    # L3：1.5× 但配日回转额上限（此处按 1.5× 计算，额度上限在建议层）
    return int(sellable * 1.5)


# ────────────────────────────────────────────────────────────────
# 三阶校验网关
# ────────────────────────────────────────────────────────────────

def _breakers_from_ctx(risk: Dict[str, Any], daily: Dict[str, Any]) -> Tuple[bool, str]:
    """从注入的 risk/daily 状态判定熔断（回测与实盘共用）。"""
    if risk.get("stop_all"):
        return True, "STOP_ALL 已触发"
    if risk.get("manual_lock"):
        return True, f"人工锁定: {risk.get('lock_reason') or 'manual'}"
    if daily.get("risk_breaker"):
        return True, daily.get("breaker_reason") or "日亏损熔断"
    if int(risk.get("consecutive_losses") or 0) >= 3:
        return True, "连续亏损 ≥ 3 次，临时禁自动"
    return False, ""


def validate_order_at(symbol: str, side: str, price: float, volume: int,
                      ctx: Dict[str, Any],
                      condition_id: Optional[int] = None,
                      trigger_id: Optional[int] = None,
                      reason: str = "",
                      decision_source: str = "agent",
                      allow_human_override: bool = False,
                      is_stop_loss: bool = False) -> Dict[str, Any]:
    """做T下单网关校验（三阶 + 二段断言）——状态全注入版（回测与实盘共用）。

    与 validate_order 行为完全一致，仅把实时依赖改为从 ctx 读取：
        regime: str             regime 档位（compute_regime().regime 或历史近似）
        quote: Optional[dict]   行情快照（current/pre_close/change_pct...；None 跳过涨跌停/追价断言）
        ledger: dict            get_sellable_ledger() 结果
        net_asset: float        t 账户净值（回测用固定假设）
        daily: dict             get_daily_state() 结果
        risk: dict              get_risk_state() 结果
        sell_in_transit: bool   卖出在途锁（回测由账本判定）
        trigger_status: Optional[str]  触发事件状态（回测由回测事件表提供；None 时查 t_triggers）
        cost_ratio_ok: Optional[bool]  价差/成本比预判（None 时内部调 _cost_ratio_ok）

    decision_source: agent/ai_led（ai_led 与 agent 同档风控，不豁免任何校验；
    区别仅在于 ai_led 允许无触发事件的主动买卖——孤儿单校验仅在 trigger_id 给定时生效）。
    """
    result = {"pass": False, "mode": "blocked", "level": "hard", "reason": "", "warn": []}
    try:
        regime = ctx.get("regime", "ACTIVE")
        quote = ctx.get("quote")
        ledger = ctx.get("ledger") or {}
        net_asset = float(ctx.get("net_asset") or 200000.0)
        daily = ctx.get("daily") or {}
        risk = ctx.get("risk") or {}

        # ── 第一阶：硬闸门（O(1) 快路径） ──
        # 1) account 白名单
        if ACCOUNT_T != "t":
            result["reason"] = "非白名单账户"
            return result
        # 2) STOP_ALL / 熔断
        broken, why = _breakers_from_ctx(risk, daily)
        if broken:
            result["reason"] = why
            return result
        # 3) 裸空/无卖腿拦截（卖出必须有持仓；买入若无底仓且为低吸则拒）
        if side == "sell":
            item = ledger.get(symbol)
            if not item or item["sellable"] < volume:
                result["reason"] = "无足够可卖底仓（裸空拦截）"
                return result
        elif side == "buy":
            # 无底仓买入：仅条件单触发路径（condition_id/trigger_id 提供）放行——
            # 迭代#58（用户需求）：未建仓标的通过监控条件命中直接建仓开仓；
            # 量受 _max_buy_volume 建仓规模上限（单笔/单标）约束。
            # 无触发事件的主动裸买（非自由跑）仍拒绝——新开仓走独立建仓流程。
            no_pos = symbol not in ledger
            if no_pos and T_BUY_TIER_LIMIT_ENABLED \
                    and not condition_id and not trigger_id:
                result["reason"] = "无底仓标的禁止裸买（新开仓走独立建仓流程或发布条件单）"
                return result
        # 4) 跌停禁买 / 涨停禁卖
        if quote:
            limit_status = _limit_status(quote, side)
            if limit_status == "block":
                result["reason"] = f"{'跌停' if side == 'buy' else '涨停'}禁单"
                return result
        # 5) 孤儿单（trigger 已 cancelled/executed 不重复下单）
        if trigger_id:
            trig_status = ctx.get("trigger_status")
            trig = {"status": trig_status} if trig_status is not None else _get_trigger(trigger_id)
            if trig and trig.get("status") not in ("pending", "claimed", "auto_ready", "human_confirm"):
                result["reason"] = f"触发事件状态异常: {trig.get('status')}"
                return result

        # ── 第二阶：账本（确定性规则） ──
        # 底仓风控（独立于做T止损）：浮亏 ≤ −3% 减半 / ≤ −5% 清仓锁定（开关可关，人工覆盖可放行）
        if T_STOP_GUARD_ENABLED and not allow_human_override:
            guard = _base_loss_guard(symbol, side, quote, ledger)
            if guard["action"] != "pass":
                result["level"] = "ledger"
                result["reason"] = guard["reason"]
                return result
        # 可卖底仓分档 + 买腿上限
        near_limit = bool(quote and _near_limit_down(quote))
        tier = _floor_tier(regime, near_limit)
        if side == "buy":
            # 低吸加仓次数上限（单标单日买腿成交 ≤ MAX_DAILY_BUY_LEGS）
            buy_legs = ctx.get("daily_buy_legs")
            if buy_legs is None:
                buy_legs = _daily_buy_legs(symbol)
            if buy_legs >= MAX_DAILY_BUY_LEGS:
                result["level"] = "ledger"
                result["reason"] = f"低吸加仓次数超限（当日已 {buy_legs} 笔 ≥ {MAX_DAILY_BUY_LEGS}）"
                return result
            max_buy = _max_buy_volume(symbol, tier, ledger, price=price,
                                      condition_id=condition_id)
            if max_buy <= 0:
                result["level"] = "ledger"
                result["reason"] = f"当前档位 {tier} 禁止低吸"
                return result
            if volume > max_buy:
                result["level"] = "ledger"
                result["reason"] = f"买腿 {volume} 超过档位 {tier} 上限 {max_buy}（买腿≤可卖底仓）"
                return result
            # 卖出在途锁：半边腿未落定不启动另一半
            if ctx.get("sell_in_transit", False):
                result["level"] = "ledger"
                result["reason"] = "卖出在途，禁止启动买腿（半边腿未落定）"
                return result
        # 日亏损熔断（第二阶重复确认）——只拦买腿（防继续加仓/开仓放大亏损），
        # 不拦卖腿：止损/高抛离场是止血动作，必须放行（否则深跌时无法止损）
        realized = float(daily.get("realized_pnl") or 0)
        pnl_pct = realized / net_asset * 100 if net_asset else 0.0
        if side == "buy" and pnl_pct <= -DAILY_LOSS_BREAKER_PCT * 100:
            result["level"] = "ledger"
            result["reason"] = f"日亏损熔断（{pnl_pct:.2f}%）"
            return result
        # 日回转额上限（主指标）——止损卖腿豁免（止血离场必须执行）；
        # T_TURNOVER_LIMIT_ENABLED=0（AI 自由跑）跳过
        turnover = float(daily.get("daily_turnover_amount") or 0)
        if T_TURNOVER_LIMIT_ENABLED \
                and turnover + price * volume > net_asset * MAX_DAILY_TURNOVER_RATIO \
                and not (side == "sell" and is_stop_loss):
            result["level"] = "ledger"
            result["reason"] = "当日累计回转额超上限"
            return result

        # ── 第三阶：建议层（仅告警/限频，不拒热路径） ──
        warns = []
        # 单笔 ≤ 净值 5%
        if price * volume > net_asset * MAX_SINGLE_ORDER_PCT:
            warns.append(f"单笔超净值5%（建议）")
        # 价差/成本比过滤（决策生成层已做，此处复核）
        cost_ok = ctx.get("cost_ratio_ok")
        if cost_ok is None:
            cost_ok = _cost_ratio_ok(symbol, price)
        if not cost_ok:
            warns.append("滑点+手续费占价差空间过高（建议不触发）")
        # 日亏预警
        if pnl_pct <= -DAILY_LOSS_WARN_PCT * 100:
            warns.append("日亏接近预警线（限频）")
        result["warn"] = warns

        # ── 二段实时断言（落单前最新） ──
        # 最新价格断言：低吸不打在远离 target 的追价上
        if side == "buy" and quote:
            current = float(quote.get("current", 0) or 0)
            if current > price * 1.02:  # 当前价高于委托价 2% 以上（追价风险）
                result["level"] = "assert"
                result["reason"] = f"最新价 {current} 高于委托价 {price} 超 2%（追价风险）"
                return result

        result.update({"pass": True, "mode": "auto", "level": "ok"})
        return result
    except Exception as e:
        result["reason"] = f"网关校验异常: {e}"
        return result


def validate_order(symbol: str, side: str, price: float, volume: int,
                   condition_id: Optional[int] = None,
                   trigger_id: Optional[int] = None,
                   reason: str = "",
                   decision_source: str = "agent",
                   is_stop_loss: bool = False) -> Dict[str, Any]:
    """做T下单网关校验（实时路径）——构造实时 ctx 后委托 validate_order_at。"""
    quote = self_quote(symbol)
    regime_state = compute_regime()
    ctx = {
        "regime": regime_state.get("regime", "ACTIVE"),
        "quote": quote,
        "ledger": get_sellable_ledger(),
        "net_asset": t_net_asset(),
        "daily": t_db.get_daily_state() or {},
        "risk": t_db.get_risk_state() or {},
        "sell_in_transit": is_sell_in_transit(symbol) if side == "buy" else False,
    }
    return validate_order_at(symbol, side, price, volume, ctx,
                             condition_id=condition_id, trigger_id=trigger_id,
                             reason=reason, decision_source=decision_source,
                             is_stop_loss=is_stop_loss)


def self_quote(symbol: str) -> Optional[dict]:
    """拉取单只实时行情（网关用）。"""
    q = fetch_tencent_quote([_normalize_symbol(symbol)])
    return q.get(_normalize_symbol(symbol))


def _limit_status(quote: dict, side: str) -> str:
    """跌停/涨停判断（腾讯 qt 无直接字段，用 change_pct 近似：|涨跌幅| ≥ 9.8% 视为封板）。"""
    try:
        chg = float(quote.get("change_pct", 0) or 0)
        if side == "buy" and chg <= -9.8:
            return "block"
        if side == "sell" and chg >= 9.8:
            return "block"
    except (TypeError, ValueError):
        pass
    return "ok"


def _near_limit_down(quote: dict) -> bool:
    try:
        chg = float(quote.get("change_pct", 0) or 0)
        return chg <= -8.0  # 接近跌停（8% 内）视为高风险区
    except (TypeError, ValueError):
        return False


def _base_loss_guard(symbol: str, side: str, quote: Optional[dict],
                     ledger: Dict[str, Dict[str, Any]]) -> Dict[str, str]:
    """底仓浮亏风控（独立于做T止损，P0-3）：买腿/建仓前先评估标的浮亏。

    - 浮亏 ≤ −5%：blocked（清仓锁定，禁止再买/加仓）
    - 浮亏 ≤ −3%：blocked 且提示先减半（卖 50%）再考虑买腿
    - 其余放行（返回 action=pass）
    现价缺失时放行（保守：不因数据缺失误伤）。
    """
    if side != "buy":
        return {"action": "pass", "reason": ""}
    item = (ledger or {}).get(symbol) or {}
    avg = float(item.get("avg_price") or 0)
    current = float((quote or {}).get("current") or 0)
    if avg <= 0 or current <= 0:
        return {"action": "pass", "reason": ""}
    pnl_pct = (current - avg) / avg * 100
    if pnl_pct <= -BASE_LOSS_CLEAR_PCT:
        return {"action": "block", "reason": f"底仓浮亏 {pnl_pct:.1f}%（≤−{BASE_LOSS_CLEAR_PCT:.0f}% 清仓锁定，禁买）"}
    if pnl_pct <= -BASE_LOSS_HALF_PCT:
        return {"action": "block", "reason": f"底仓浮亏 {pnl_pct:.1f}%（≤−{BASE_LOSS_HALF_PCT:.0f}% 先减半仓再考虑）"}
    return {"action": "pass", "reason": ""}


def _daily_buy_legs(symbol: str) -> int:
    """单标当日低吸（买腿）成交次数：paper_trades 当日买入笔数（t 账户）。"""
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            n = db.execute(text(
                "SELECT COUNT(*) FROM paper_trades "
                "WHERE account_id = 't' AND direction = '买入' AND voided = 0 "
                "AND substr(created_at, 1, 10) = :today AND symbol = :sym"
            ), {"today": today, "sym": symbol}).scalar()
            return int(n or 0)
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 低吸次数查询失败: {e}")
        return 0


def _cost_ratio_ok(symbol: str, price: float) -> bool:
    """滑点+手续费 vs 价差空间：>20% 不值得做。"""
    try:
        from app.services.t_pool import calc_t_quality
        q = calc_t_quality(symbol)
        spread = float(q.get("spread", 0) or 0)
        cost_pct = SLIPPAGE_PCT * 2 + 0.001  # 双边滑点 + 手续费
        if spread <= 0:
            return False
        return cost_pct / (spread / 100) <= COST_RATIO_LIMIT
    except Exception:
        return True


def _get_trigger(trigger_id: int) -> Optional[dict]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT * FROM t_triggers WHERE id = :id"
            ), {"id": trigger_id}).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-gate] 查询触发事件失败: {e}")
        return None


# ────────────────────────────────────────────────────────────────
# 异常升级判定（6 类清单）
# ────────────────────────────────────────────────────────────────

def classify_escalation(symbol: str, side: str, trigger: Optional[dict] = None,
                        regime: str = "ACTIVE") -> Tuple[str, str]:
    """异常升级 6 类清单 → 返回 (是否升级, 原因)。升级目标：agent / human。

    ①软件异常 ②歧义 ③首开非底仓 ④regime极端 ⑤连续触风控 ⑥孤儿单
    """
    risk = t_db.get_risk_state() or {}

    # ④ regime=极端 → human
    if regime == "HALT":
        return "human", "regime=HALT 极端市况强制人工"

    # ⑤ 连续触风控
    if int(risk.get("consecutive_losses") or 0) >= 2:
        return "human", "连续触犯风控，强制人工+临时禁自动"

    # ③ 首次对非底仓池标的触发（无底仓）
    ledger = get_sellable_ledger()
    if side == "buy" and symbol not in ledger:
        return "human", "首开非底仓标的（新开仓风险）"

    # ⑥ 孤儿单/账实不一致
    if trigger and trigger.get("status") == "claimed" and trigger.get("claimed_at"):
        claimed = trigger["claimed_at"]
        try:
            if isinstance(claimed, str):
                claimed_dt = datetime.strptime(claimed, "%Y-%m-%d %H:%M:%S")
            else:
                claimed_dt = claimed
            if (datetime.now() - claimed_dt).total_seconds() > TRIGGER_EXEC_TIMEOUT_MIN * 60:
                return "human", "孤儿单超时未确认（账实漂移风险）"
        except (ValueError, TypeError):
            pass

    # ② 歧义：接近熔断线
    pnl_pct = _daily_pnl_pct()
    if pnl_pct <= -DAILY_LOSS_WARN_PCT * 100:
        return "agent", f"接近日亏预警线（{pnl_pct:.2f}%）需复核"

    # ① 软件异常由网关内嵌处理（此处无独立触发）
    return "auto", ""


# ────────────────────────────────────────────────────────────────
# 网关执行入口（供 Agent/Worker 调用，最终放行者）
# ────────────────────────────────────────────────────────────────

def gateway_execute(symbol: str, side: str, price: float, volume: int,
                    condition_id: Optional[int] = None,
                    trigger_id: Optional[int] = None,
                    reason: str = "",
                    decision_source: str = "agent",
                    is_stop_loss: bool = False) -> Dict[str, Any]:
    """做T下单唯一入口：网关校验通过才调用执行器撮合。

    三权分立：Agent/AI 决策后提交 → 本网关（唯一放行者）→ MarcusVNPyExecutor(account_id='t')。
    decision_source: agent（触发复核路径）/ ai_led（AI 主动决策，无触发事件也可下单）。
    is_stop_loss: 止损离场卖腿——豁免日亏损熔断/回转额上限（止血动作必须执行）。
    执行器失败/被拒 → 更新 t_triggers 为 blocked + 审计。
    """
    from app.core.trading.marcus_trade import MarcusVNPyExecutor
    from paper_engine import PaperTradingEngine
    from workspace_detector import DATA_DIR

    # 1) 校验
    check = validate_order(symbol, side, price, volume,
                           condition_id=condition_id, trigger_id=trigger_id,
                           reason=reason, decision_source=decision_source,
                           is_stop_loss=is_stop_loss)
    if not check["pass"]:
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "blocked", reason=check["reason"])
        return {"status": "rejected", "reason": check["reason"], "level": check.get("level")}

    # 2) 执行器撮合（account_id='t'）
    engine = PaperTradingEngine(data_dir=str(DATA_DIR), account_id=ACCOUNT_T)
    executor = MarcusVNPyExecutor(engine=engine, account_id=ACCOUNT_T)
    try:
        if side == "buy":
            result = executor.buy(symbol=symbol, price=price, volume=volume, reason=reason or "做T低吸")
        else:
            result = executor.sell(symbol=symbol, price=price, volume=volume, reason=reason or "做T高抛")
    except Exception as e:
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "blocked", reason=f"执行异常: {e}")
        return {"status": "rejected", "reason": f"执行异常: {e}"}

    # 3) 结果回写
    ok = result.get("status") == "success" or result.get("status") == "filled"
    if ok:
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "executed",
                                       executed_price=float(result.get("price", price) or price))
        # 更新日账本
        _update_daily_ledger(symbol, side, price, volume)
        return {"status": "success", **result}
    if trigger_id:
        t_db.update_trigger_status(trigger_id, "blocked", reason=result.get("reason") or "撮合失败")
    return {"status": "rejected", "reason": result.get("reason") or "撮合失败", **result}


def _update_daily_ledger(symbol: str, side: str, price: float, volume: int):
    """更新 t_daily_state（累计回转额/买卖计数；realized_pnl 由引擎成交推送补全）。"""
    try:
        daily = t_db.get_daily_state() or {}
        amount = float(daily.get("daily_turnover_amount") or 0) + price * volume
        buy_count = int(daily.get("buy_count") or 0) + (1 if side == "buy" else 0)
        sell_count = int(daily.get("sell_count") or 0) + (1 if side == "sell" else 0)
        t_db.upsert_daily_state({
            "daily_turnover_amount": round(amount, 2),
            "buy_count": buy_count,
            "sell_count": sell_count,
        })
    except Exception as e:
        print(f"[t-gate] 日账本更新失败: {e}")
