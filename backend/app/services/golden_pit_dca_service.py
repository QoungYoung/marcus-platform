# -*- coding: utf-8 -*-
"""黄金坑 DCA 自动定投执行服务。

每个交易日触发，检查黄金坑窗口状态，对处于黄金坑的宽基 ETF 按策略执行定投买入。

策略权重（基于回测结论）:
  - uniform_10: 前 10 日等权重（推荐，综合表现最好）
  - uniform_7:  前 7 日等权重
  - uniform_5:  前 5 日等权重
  - uniform_3:  前 3 日等权重
  - front_loaded: 递减（前几天多投）
  - triangle:     三角（第 7-8 天最多）
"""

import logging
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

PIT_WINDOW_DAYS = 15
PRE_TURN_CUMULATIVE_CAP = 0.15   # 拐点前累计上限 (占 max_total 比例)
TRADE_API_BASE = "http://127.0.0.1:8000/api/v1"


def _strategy_weights(strategy: str) -> List[float]:
    """生成策略权重向量（15 天窗口）。"""
    n = PIT_WINDOW_DAYS

    if strategy == "uniform_3":
        return [1.0 / 3] * 3 + [0.0] * 12
    elif strategy == "uniform_5":
        return [1.0 / 5] * 5 + [0.0] * 10
    elif strategy == "uniform_7":
        return [1.0 / 7] * 7 + [0.0] * 8
    elif strategy == "uniform_10":
        return [1.0 / 10] * 10 + [0.0] * 5
    elif strategy == "uniform_15":
        return [1.0 / n] * n
    elif strategy == "front_loaded":
        raw = [n - i for i in range(n)]
        total = sum(raw)
        return [r / total for r in raw]
    elif strategy == "back_loaded":
        raw = [i + 1 for i in range(n)]
        total = sum(raw)
        return [r / total for r in raw]
    elif strategy == "triangle":
        mid = 7
        raw = [(i + 1) if i < mid else (n - i) for i in range(n)]
        total = sum(raw)
        return [r / total for r in raw]
    elif strategy == "lump_entry":
        w = [0.0] * n
        w[0] = 1.0
        return w
    else:
        return [1.0 / 10] * 10 + [0.0] * 5


def _get_etf_configs() -> List[Dict[str, Any]]:
    """从数据库读取启用的 ETF 配置。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_etf_config import GoldenPitETFConfig

        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitETFConfig)
                .filter(GoldenPitETFConfig.enabled == True)
                .order_by(GoldenPitETFConfig.priority)
                .all()
            )
            return [
                {
                    "fund_code": r.fund_code,
                    "index_name": r.index_name,
                    "etf_code": r.etf_code,
                    "etf_name": r.etf_name,
                    "strategy": r.strategy,
                    "daily_amount": r.daily_amount,
                    "max_total_amount": r.max_total_amount,
                    "require_absolute_threshold": r.require_absolute_threshold,
                    "min_days_in_pit": r.min_days_in_pit,
                    "skip_if_already_holding": r.skip_if_already_holding,
                }
                for r in rows
            ]
        finally:
            db.close()
    except Exception as e:
        logger.warning("读取黄金坑 ETF 配置失败: %s", e)
        return []


def _get_executed_days(fund_code: str, window_start: str) -> set:
    """查询本窗口已执行的定投日，防止重复买入。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.fund_code == fund_code,
                    GoldenPitDCALog.window_start == window_start,
                    GoldenPitDCALog.status == "filled",
                )
                .all()
            )
            return {r.buy_day for r in rows}
        finally:
            db.close()
    except Exception:
        return set()


def _already_holding(etf_code: str) -> bool:
    """检查是否已持有该 ETF（paper_positions 表有记录即表示持仓）。"""
    try:
        from app.database import SessionLocal
        from app.models.paper_trade import PaperPosition
        import re

        db = SessionLocal()
        try:
            code = re.sub(r'^(SH|SZ|BJ)', '', etf_code)
            pos = (
                db.query(PaperPosition)
                .filter(PaperPosition.symbol == etf_code)
                .first()
            )
            if not pos and code != etf_code:
                pos = (
                    db.query(PaperPosition)
                    .filter(PaperPosition.symbol == code)
                    .first()
                )
            return pos is not None
        finally:
            db.close()
    except Exception:
        return False


def _place_buy_order(etf_code: str, amount: float, reason: str) -> Tuple[bool, str]:
    """通过交易 API 下限价买入单（限价 × 1.02 以保证成交）。"""
    try:
        # 先获取当前价格 (GET /market/quote/{symbol} 返回 QuoteResponse)
        quote_url = f"{TRADE_API_BASE}/market/quote/{etf_code}"
        quote_resp = requests.get(quote_url, timeout=10)
        current_price = None
        if quote_resp.status_code == 200:
            quote_data = quote_resp.json()
            # QuoteResponse 直接返回字段，没有 code/data 包装
            current_price = quote_data.get("current") or quote_data.get("last_close")

        if not current_price or current_price <= 0:
            logger.warning("无法获取 %s 当前价格，跳过定投", etf_code)
            return False, "无法获取当前价格"

        # 计算买入股数（100 股整数倍）
        shares = int(amount / current_price / 100) * 100
        if shares < 100:
            logger.warning("%s 定投金额 %.0f 不足买入 1 手，跳过", etf_code, amount)
            return False, f"金额不足: {amount:.0f} < 1手({current_price * 100:.0f})"

        # 限价单: 当前价 × 1.02 以保证成交
        limit_price = round(current_price * 1.02, 2)

        trade_url = f"{TRADE_API_BASE}/trades"
        trade_data = {
            "symbol": etf_code,
            "side": "buy",
            "price": limit_price,
            "volume": shares,
            "reason": reason,
        }
        trade_resp = requests.post(trade_url, json=trade_data, timeout=30)

        if trade_resp.status_code == 200:
            result = trade_resp.json()
            # TradeResponse: { order_id, status, symbol, direction, price, volume, ... }
            status = result.get("status", "")
            if status in ("filled", "partial", "submitted"):
                order_id = result.get("order_id", "")
                logger.info("黄金坑 DCA 买入 %s: %d股 @%.2f, order=%s", etf_code, shares, limit_price, order_id)
                return True, order_id
            else:
                msg = result.get("message") or result.get("reason") or status
                logger.warning("黄金坑 DCA 买入 %s 失败: %s", etf_code, msg)
                return False, msg or "unknown"
        else:
            return False, f"HTTP {trade_resp.status_code}"
    except Exception as e:
        logger.error("黄金坑 DCA 买入 %s 异常: %s", etf_code, e)
        return False, str(e)


def _place_sell_order(etf_code: str, shares: int, reason: str) -> Tuple[bool, str]:
    """通过交易 API 下限价卖单（限价 × 0.98 以保证成交）。"""
    try:
        quote_url = f"{TRADE_API_BASE}/market/quote/{etf_code}"
        quote_resp = requests.get(quote_url, timeout=10)
        current_price = None
        if quote_resp.status_code == 200:
            quote_data = quote_resp.json()
            current_price = quote_data.get("current") or quote_data.get("last_close")

        if not current_price or current_price <= 0:
            logger.warning("无法获取 %s 当前价格，跳过卖出", etf_code)
            return False, "无法获取当前价格"

        if shares < 100:
            logger.warning("%s 卖出股数 %d 不足 1 手，跳过", etf_code, shares)
            return False, f"股数不足: {shares} < 100"

        limit_price = round(current_price * 0.98, 2)

        trade_url = f"{TRADE_API_BASE}/trades"
        trade_data = {
            "symbol": etf_code,
            "side": "sell",
            "price": limit_price,
            "volume": shares,
            "reason": reason,
        }
        trade_resp = requests.post(trade_url, json=trade_data, timeout=30)

        if trade_resp.status_code == 200:
            result = trade_resp.json()
            status = result.get("status", "")
            if status in ("filled", "partial", "submitted"):
                order_id = result.get("order_id", "")
                logger.info("黄金坑 DCA 卖出 %s: %d股 @%.2f, order=%s", etf_code, shares, limit_price, order_id)
                return True, order_id
            else:
                msg = result.get("message") or result.get("reason") or status
                logger.warning("黄金坑 DCA 卖出 %s 失败: %s", etf_code, msg)
                return False, msg or "unknown"
        else:
            return False, f"HTTP {trade_resp.status_code}"
    except Exception as e:
        logger.error("黄金坑 DCA 卖出 %s 异常: %s", etf_code, e)
        return False, str(e)


def _get_holding_shares(etf_code: str) -> int:
    """查询当前持有的 ETF 股数。"""
    try:
        from app.database import SessionLocal
        from app.models.paper_trade import PaperPosition
        import re

        db = SessionLocal()
        try:
            pos = (
                db.query(PaperPosition)
                .filter(PaperPosition.symbol == etf_code)
                .first()
            )
            if not pos:
                code = re.sub(r'^(SH|SZ|BJ)', '', etf_code)
                pos = (
                    db.query(PaperPosition)
                    .filter(PaperPosition.symbol == code)
                    .first()
                )
            return pos.volume if pos else 0
        finally:
            db.close()
    except Exception:
        return 0


def _record_dca_log(
    fund_code: str,
    window_start: str,
    buy_day: int,
    etf_code: str,
    amount: float,
    strategy: str,
    order_id: str,
    status: str,
):
    """记录 DCA 执行日志到数据库。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        db = SessionLocal()
        try:
            log = GoldenPitDCALog(
                fund_code=fund_code,
                window_start=window_start,
                buy_day=buy_day,
                etf_code=etf_code,
                amount=amount,
                strategy=strategy,
                order_id=order_id,
                status=status,
                created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
            db.add(log)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("记录 DCA 日志失败: %s", e)


def _resonance_multiplier(indices: List[Dict]) -> float:
    """多指数共振系数: 入坑指数越多，信号越可靠，仓位越高。

    golden_pit (P5 内) 指数计数:
      4+ → 1.3x (强共振)
      3  → 1.2x
      2  → 1.0x (标准)
      1  → 0.6x (弱共振，单兵作战)
    """
    pit_count = sum(
        1 for i in indices
        if i.get("tier") in ("core", "satellite", "defense")
        and i["status"] == "golden_pit"
    )
    if pit_count >= 4:
        return 1.3
    elif pit_count >= 3:
        return 1.2
    elif pit_count >= 2:
        return 1.0
    else:
        return 0.6


def execute_golden_pit_dca() -> str:
    """
    黄金坑 DCA 定投主逻辑 v4 — 趋势驱动仓位管理。

    阶段:
      waiting — 黄金坑信号活跃但拐点未确认 → 轻仓累积
      buying  — 拐点确认 → 按回升天数分级加仓

    仓位逻辑:
      拐点前 (greed仍在下降):
        → 轻仓累积: 单次 ≤ max_total * 3%, 累计 ≤ max_total * 15%
      拐点确认 (greed连续2天回升):
        → 2天 (turning): 50% → 3天 (accelerate): 75% → 4+天 (full): 100%

    跳过 drop(放弃) 和 watch(仅观察) 级别的指数。

    Returns:
        执行结果摘要字符串。
    """
    from app.services.golden_pit_service import GoldenPitService, POSITION_TIERS, CHINA_INDICES

    # 1. 获取黄金坑状态
    try:
        gp_service = GoldenPitService()
        status = gp_service.get_status()
    except Exception as e:
        msg = f"获取黄金坑状态失败: {e}"
        logger.error(msg)
        return msg

    window = status.get("golden_pit_window", {})
    indices = status.get("indices", [])
    as_of = status.get("as_of", "")

    phase = window.get("phase", "idle")
    if phase == "idle":
        return f"[{as_of}] 无黄金坑信号，跳过 DCA 定投"

    current_day = window.get("current_day", 0)
    window_start = window.get("start_date", "")
    today_str = datetime.now().strftime("%Y-%m-%d")

    logger.info(
        "黄金坑 DCA: phase=%s day=%d, start=%s, leading=%s(%s), pit=%d, warning=%d, turning=%d",
        phase, current_day, window_start,
        window.get("leading_index"), window.get("leading_tier", ""),
        window.get("pit_count", 0), window.get("warning_count", 0),
        window.get("turning_count", 0),
    )

    # 2. 读取 ETF 配置
    etf_configs = _get_etf_configs()
    if not etf_configs:
        return "无启用的黄金坑 ETF 配置"

    config_by_fund = {c["fund_code"]: c for c in etf_configs}

    # 3. 筛选: 只交易 core/satellite/defense 级别的指数
    tradeable = [
        i for i in indices
        if i.get("tier") in ("core", "satellite", "defense")
        and i.get("position_tier") is not None
        and i["status"] in ("warning", "golden_pit")
    ]
    # 按 tier 优先: core > satellite > defense, 同 tier 按 priority
    tier_order = {"core": 0, "satellite": 1, "defense": 2}
    tradeable.sort(key=lambda x: (tier_order.get(x.get("tier"), 9), x["priority"]))

    results = []
    executed_count = 0
    skipped_count = 0
    total_invested_today = 0.0

    # ── 退出信号检查: 对已持仓 ETF 检查是否需要卖出 ──
    for idx in indices:
        exit_signal = idx.get("exit_signal")
        if not exit_signal:
            continue
        fund_code = idx["fund_code"]
        cfg = config_by_fund.get(fund_code)
        if not cfg:
            continue
        etf_code = cfg["etf_code"]
        holding = _get_holding_shares(etf_code)
        if holding < 100:
            continue

        if exit_signal in ("full_exit", "stop_profit"):
            sell_shares = holding
        elif exit_signal == "half_exit":
            sell_shares = int(holding / 2 / 100) * 100
        else:
            continue

        if sell_shares < 100:
            continue

        sell_reason = (
            f"[黄金坑DCA 退出] {idx['index_name']} {idx.get('exit_reason', '')} "
            f"greed={idx['greed']:.4f} P{idx['percentile']:.0f}"
        )
        success, order_id = _place_sell_order(etf_code, sell_shares, sell_reason)

        _record_dca_log(
            fund_code=fund_code,
            window_start=window_start,
            buy_day=current_day,
            etf_code=etf_code,
            amount=0,
            strategy=f"exit/{exit_signal}",
            order_id=order_id if success else "",
            status="filled" if success else "failed",
        )

        exit_icon = {"half_exit": "🟡", "full_exit": "🔴", "stop_profit": "🟠"}.get(exit_signal, "")
        if success:
            results.append(
                f"{exit_icon} 退出 {idx['index_name']} {etf_code}: "
                f"卖出 {sell_shares}股 [{exit_signal}]"
            )
        else:
            results.append(f"❌ 退出 {idx['index_name']} {etf_code}: 卖出失败 - {order_id}")

    for idx in tradeable:
        fund_code = idx["fund_code"]
        cfg = config_by_fund.get(fund_code)
        if not cfg:
            continue

        # 绝对阈值双重确认
        if cfg["require_absolute_threshold"] and not idx.get("absolute_triggered"):
            logger.debug("%s: 要求绝对阈值双重确认，跳过", fund_code)
            skipped_count += 1
            continue

        # 最小入坑天数
        days_in = idx.get("days_in_pit") or idx.get("days_in_warning") or 0
        if days_in < cfg["min_days_in_pit"]:
            logger.debug("%s: 仅 %d 天在坑，需要 ≥%d 天", fund_code, days_in, cfg["min_days_in_pit"])
            skipped_count += 1
            continue

        # 已持仓跳过
        if cfg["skip_if_already_holding"] and _already_holding(cfg["etf_code"]):
            logger.info("%s: 已持有 %s，跳过", fund_code, cfg["etf_code"])
            skipped_count += 1
            continue

        # 检查今日是否已执行
        executed_days = _get_executed_days(fund_code, window_start)
        if current_day in executed_days:
            logger.info("%s: 第 %d 天已执行，跳过", fund_code, current_day)
            skipped_count += 1
            continue

        # ── 分级仓位计算 v4 (纯趋势驱动) ──
        max_total = cfg["max_total_amount"]
        turning_confirmed = idx.get("turning_point_confirmed", False)
        days_rising = idx.get("days_rising", 0)
        trend = idx.get("trend", "declining")

        # 获取分指数参数
        index_params = CHINA_INDICES.get(fund_code, {})
        pos_mult = index_params.get("position_multiplier", 1.0)
        idx_pre_turn_cap = index_params.get("pre_turn_cap", PRE_TURN_CUMULATIVE_CAP)

        # 累计已投
        total_invested = sum(
            _get_day_amount(fund_code, window_start, d) for d in executed_days
        )
        remaining = max_total - total_invested
        if remaining <= 0:
            logger.info("%s: 累计已达上限 %.0f，跳过", fund_code, max_total)
            skipped_count += 1
            continue

        if not turning_confirmed:
            # ── 拐点前: 轻仓累积, 单次≤3%, 累计由分指数参数决定 ──
            pre_turn_max = max_total * idx_pre_turn_cap
            if total_invested >= pre_turn_max:
                logger.info("%s: 拐点前累计已达上限 %.0f(%.0f%%), 等待拐点确认",
                            fund_code, pre_turn_max, idx_pre_turn_cap * 100)
                skipped_count += 1
                continue
            tier_multiplier = POSITION_TIERS.get("pre_turn", 0.03) * pos_mult
            daily_amount = min(max_total * tier_multiplier, pre_turn_max - total_invested)
            position_tier = "pre_turn"
        else:
            # ── 拐点后: 快速加仓, 50%→75%→100%, 应用分指数倍率 ──
            if days_rising >= 4:
                tier_multiplier = POSITION_TIERS.get("full", 1.00) * pos_mult
                position_tier = "full"
            elif days_rising >= 3:
                tier_multiplier = POSITION_TIERS.get("accelerate", 0.75) * pos_mult
                position_tier = "accelerate"
            else:
                tier_multiplier = POSITION_TIERS.get("turning", 0.50) * pos_mult
                position_tier = "turning"
            daily_amount = max_total * tier_multiplier
            daily_amount = min(daily_amount, remaining)

        # 共振系数
        resonance = _resonance_multiplier(indices)
        daily_amount = min(daily_amount * resonance, max_total - total_invested)

        etf_code = cfg["etf_code"]
        reason = (
            f"[黄金坑DCA v4] {idx['index_name']} "
            f"tier={idx.get('tier')} trend={trend} pos={position_tier}({tier_multiplier*100:.0f}%) "
            f"resonance={resonance}x day{current_day}/{PIT_WINDOW_DAYS} greed={idx['greed']:.4f}"
        )

        logger.info(
            "黄金坑 DCA: %s day=%d trend=%s amount=%.0f tier=%s x%.0f%% resonance=%.1fx",
            etf_code, current_day, trend, daily_amount, position_tier, tier_multiplier * 100, resonance,
        )
        success, order_id = _place_buy_order(etf_code, daily_amount, reason)

        # 记录日志
        _record_dca_log(
            fund_code=fund_code,
            window_start=window_start,
            buy_day=current_day,
            etf_code=etf_code,
            amount=daily_amount,
            strategy=f"{idx.get('tier')}/{position_tier}/{trend}",
            order_id=order_id if success else "",
            status="filled" if success else "failed",
        )

        if success:
            executed_count += 1
            total_invested_today += daily_amount
            results.append(
                f"✅ {idx['index_name']} {etf_code}: "
                f"¥{daily_amount:.0f} [{idx.get('tier')}/{position_tier}] (第{current_day}天)"
            )
        else:
            results.append(f"❌ {idx['index_name']} {etf_code}: 买入失败 - {order_id}")

    # 4. 摘要
    skipped_drop = sum(1 for i in indices if i.get("tier") in ("drop", "watch") and i["status"] != "normal")
    turning_count = window.get("turning_count", 0)
    pre_turn_count = len(tradeable) - turning_count
    pit_count = window.get("pit_count", 0)
    phase = window.get("phase", "idle")
    phase_label = {"buying": "买入窗口", "waiting": "等待拐点"}.get(phase, phase)
    resonance = _resonance_multiplier(indices)
    summary_lines = [
        f"黄金坑 DCA v4 (趋势驱动) — {today_str}",
        f"阶段: {phase_label} | 领先: {window.get('leading_index', 'N/A')}",
        f"可交易: {len(tradeable)} | 拐点已确认: {turning_count} | 拐点前: {pre_turn_count}",
        f"共振: {pit_count}指数入坑 → {resonance:.1f}x 仓位系数",
        f"执行: {executed_count}笔 ¥{total_invested_today:.0f} | 跳过: {skipped_count}笔",
        f"放弃/watch: {skipped_drop}个指数不入金",
        "",
    ]
    if results:
        summary_lines.extend(results)
    else:
        summary_lines.append("本日无符合条件的定投")
    summary_lines.append("")

    # 仓位建议 (趋势驱动)
    if pre_turn_count > 0 and turning_count == 0:
        summary_lines.append("💡 拐点前: 轻仓累积(单次≤3%/累计≤15%), 等待贪婪值连续回升。")
    elif turning_count > 0 and pre_turn_count > 0:
        summary_lines.append(f"💡 部分拐点确认: {turning_count}个指数已回升加仓, {pre_turn_count}个仍在等待。")
    elif turning_count > 0:
        summary_lines.append(f"💡 拐点已确认: {turning_count}个指数快速加仓中 (50%→75%→100%)。")

    return "\n".join(summary_lines)


def _get_day_amount(fund_code: str, window_start: str, buy_day: int) -> float:
    """查询指定日已投入的金额。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.fund_code == fund_code,
                    GoldenPitDCALog.window_start == window_start,
                    GoldenPitDCALog.buy_day == buy_day,
                    GoldenPitDCALog.status == "filled",
                )
                .all()
            )
            return sum(r.amount for r in rows)
        finally:
            db.close()
    except Exception:
        return 0.0
