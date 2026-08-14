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

import json
import logging
import re
import sys
import os
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from app.services.golden_pit_config import (
    CHINA_INDICES,
    DEFENSE_INDICES,
    DEFENSE_TAKEOVER_WEIGHTS,
    PIT_POSITION_SPLIT,
    SECTOR_EXIT_DOWN_DAYS,
    SEMI_BOOST_INDICES,
    get_effective_index_config,
)
from app.services import golden_pit_sector_service as _sector

PIT_WINDOW_DAYS = 15
# 板块选筹回退宽基状态: fund_code -> 最近一次回退日期（用于『信号恢复切回』标注）
_sector_fallback_state: Dict[str, str] = {}
# 载体切换状态: fund_code -> {carrier, as_of}（相邻交易日载体不同则标注切换）
_carrier_state: Dict[str, Dict[str, str]] = {}
PRE_TURN_CUMULATIVE_CAP = 0.15   # 拐点前累计上限 (占 max_total 比例)


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


def _encode_strategy(dca_strategy: str, trend: str, trend_factor: float,
                     brake_type: str = "", buy_time: str = "") -> str:
    """编码 DCA 日志 strategy 字段: dca策略/趋势状态/因子[/制动类型][/time=HH:MM]"""
    parts = [dca_strategy, trend, f"{trend_factor:.1f}x"]
    if brake_type:
        parts.append(brake_type)
    if buy_time:
        parts.append(f"time={buy_time}")
    return "/".join(parts)


def _trend_state_label(days_rising: int) -> str:
    """趋势状态 → 可读标签 (用于日志和前端展示)。"""
    if days_rising >= 4:
        return "强势上涨"
    elif days_rising >= 3:
        return "趋势加速"
    elif days_rising >= 2:
        return "拐点确认"
    elif days_rising >= 1:
        return "触底回升"
    else:
        return "跌势未止"


def _get_buy_time(fund_code: str, days_in_pit: int = 0) -> str:
    """获取指数当前的目标买入时间 (HH:MM)。

    黄金坑日 (days_in_pit > 0) 优先使用 buy_time_pit，否则使用 buy_time。
    若均未配置，回退到系统默认值 09:36。
    """
    from app.services.golden_pit_service import CHINA_INDICES
    cfg = CHINA_INDICES.get(fund_code, {})
    is_pit_day = days_in_pit > 0
    if is_pit_day and cfg.get("buy_time_pit"):
        return cfg["buy_time_pit"]
    return cfg.get("buy_time", "09:36")


def _time_matches_slot(target_time: str, slot: str) -> bool:
    """检查目标买入时间是否落入当前批次的时间窗口。

    早盘窗口: 09:35-09:40
    尾盘窗口: 14:15-14:55
    """
    if not target_time or len(target_time) < 5:
        return True
    try:
        h, m = int(target_time[:2]), int(target_time[3:5])
    except ValueError:
        return True
    minutes = h * 60 + m

    if slot == "morning":
        return 9 * 60 + 35 <= minutes <= 9 * 60 + 40
    elif slot == "afternoon":
        return 14 * 60 + 15 <= minutes <= 14 * 60 + 55
    return True


def _get_prev_greed(fund_code: str, indices: List[Dict]) -> Optional[float]:
    """获取该指数前一交易日的贪婪值 (用于飞刀保护)。"""
    for idx in indices:
        if idx.get("fund_code") == fund_code:
            return idx.get("prev_greed")
    return None


def _check_lump_reversal(fund_code: str, schedule_day: int, dca_strategy: str,
                         window_start: str, current_greed: float) -> Tuple[bool, str]:
    """检测 lump_entry 执行后 3 天内是否出现连续 2 天贪婪下降的反转模式。

    Returns (should_reverse, reason).
    """
    if dca_strategy != "lump_entry":
        return False, ""
    if schedule_day < 1 or schedule_day > 3:
        return False, ""

    # 检查 day 0 是否有实际买入
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog
        from app.models.golden_pit import GoldenPitSnapshot

        db = SessionLocal()
        try:
            day0_buy = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.fund_code == fund_code,
                    GoldenPitDCALog.window_start == window_start,
                    GoldenPitDCALog.schedule_day == 0,
                    GoldenPitDCALog.amount > 0,
                    GoldenPitDCALog.strategy.notlike("exit/%"),
                )
                .first()
            )
            if not day0_buy:
                return False, ""

            # 检查是否已经触发过反转
            already_reversed = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.fund_code == fund_code,
                    GoldenPitDCALog.window_start == window_start,
                    GoldenPitDCALog.strategy.contains("lump_reversal"),
                )
                .first()
            )
            if already_reversed:
                return False, ""

            # 查询最近 4 个交易日的 snapshot greed 值
            snapshots = (
                db.query(GoldenPitSnapshot)
                .filter(GoldenPitSnapshot.fund_code == fund_code)
                .order_by(GoldenPitSnapshot.date.desc())
                .limit(4)
                .all()
            )
            if len(snapshots) < 3:
                return False, ""

            greeds = [s.greed for s in snapshots]  # 最新在前: [today, yesterday, day-2, day-3]

            # 检测连续 2 天下降: today < yesterday AND yesterday < day-2
            if len(greeds) >= 3:
                if greeds[0] < greeds[1] and greeds[1] < greeds[2]:
                    logger.info(
                        "%s: lump_entry 反转检测触发 (greed序列: %.4f→%.4f→%.4f, day%d)",
                        fund_code, greeds[2], greeds[1], greeds[0], schedule_day,
                    )
                    return True, f"greed={greeds[2]:.4f}→{greeds[1]:.4f}→{greeds[0]:.4f}"

        finally:
            db.close()
    except Exception as e:
        logger.warning("%s: lump_entry 反转检测查询失败: %s", fund_code, e)

    return False, ""


def _check_window_reset_count(fund_code: str, window_start: str) -> int:
    """查询当前窗口已被重置的次数。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        db = SessionLocal()
        try:
            count = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.fund_code == fund_code,
                    GoldenPitDCALog.window_start == window_start,
                    GoldenPitDCALog.strategy.contains("window_reset"),
                )
                .count()
            )
            return count
        finally:
            db.close()
    except Exception:
        return 0


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
                    GoldenPitDCALog.status.in_(("filled", "notified")),
                    GoldenPitDCALog.strategy.notlike("exit/%"),
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
                .filter(
                    PaperPosition.account_id == 'golden_pit',
                    PaperPosition.symbol == etf_code,
                )
                .first()
            )
            if not pos and code != etf_code:
                pos = (
                    db.query(PaperPosition)
                    .filter(
                        PaperPosition.account_id == 'golden_pit',
                        PaperPosition.symbol == code,
                    )
                    .first()
                )
            return pos is not None
        finally:
            db.close()
    except Exception:
        return False


def _get_simulated_position_amount(etf_code: str) -> float:
    """Simulated position from DCA logs: buy(filled/notified) minus sell(exit/)."""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog
        import re as _re

        code_no = _re.sub(r'^(SH|SZ|BJ)', '', etf_code)
        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.etf_code.in_([etf_code, code_no]),
                    GoldenPitDCALog.status.in_(("filled", "notified")),
                )
                .all()
            )
        finally:
            db.close()
        total = 0.0
        for r in rows:
            if r.strategy and r.strategy.startswith("exit/"):
                total -= r.amount
            else:
                total += r.amount
        return max(total, 0.0)
    except Exception:
        return 0.0


def _has_exit_notice(fund_code: str) -> bool:
    """True if an exit notice was already recorded for this fund today."""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        today = datetime.now().strftime("%Y-%m-%d")
        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.fund_code == fund_code,
                    GoldenPitDCALog.strategy.startswith("exit/"),
                )
                .all()
            )
            return any((r.created_at or "").startswith(today) for r in rows)
        finally:
            db.close()
    except Exception:
        return False


def _get_sector_holdings(fund_code: str) -> List[Dict[str, Any]]:
    """查询 guide_only 宽基名下的板块 ETF 模拟持仓（buy 减 exit）。

    返回项含 carrier 标记: 该 etf 活跃持仓是否全部来自 fixed_combo(carrier) 载体
    （板块连跌退出据此跳过 carrier 腿，carrier 只按宽基窗口退出）。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.fund_code == fund_code,
                    GoldenPitDCALog.status.in_(("filled", "notified")),
                )
                .all()
            )
        finally:
            db.close()
        totals: Dict[str, float] = {}
        carrier_totals: Dict[str, float] = {}
        for r in rows:
            if r.strategy and r.strategy.startswith("exit/"):
                totals[r.etf_code] = totals.get(r.etf_code, 0.0) - r.amount
                if "/carrier/" in r.strategy:
                    carrier_totals[r.etf_code] = carrier_totals.get(r.etf_code, 0.0) - r.amount
            else:
                totals[r.etf_code] = totals.get(r.etf_code, 0.0) + r.amount
                if "/carrier/" in r.strategy:
                    carrier_totals[r.etf_code] = carrier_totals.get(r.etf_code, 0.0) + r.amount
        out = []
        for code, amt in totals.items():
            if amt <= 100:
                continue
            active = max(amt, 0.0)
            c_active = max(carrier_totals.get(code, 0.0), 0.0)
            out.append({
                "etf_code": code,
                "amount": round(active, 2),
                "carrier": c_active >= active - 1e-6,  # 活跃持仓全部来自 fixed_combo(carrier) 载体
            })
        return out
    except Exception:
        return []


def _check_sector_down_turn(etf_code: str, down_days: Optional[int] = None) -> bool:
    """板块 ETF 二次拐点: 最近 N 日收盘连续回落（价格驱动）。"""
    try:
        from app.services.golden_pit_service import GoldenPitService
        bars = GoldenPitService._fetch_pi_server_kline(etf_code, limit=40)
        closes = [float(b["close"]) for b in bars if b.get("close")]
        n = down_days or int(_sector.get_sector_params(etf_code).get("exit_down_days")
                             or _sector.get_sector_config().get("exit_down_days", SECTOR_EXIT_DOWN_DAYS))
        if len(closes) < n + 1:
            return False
        return all(closes[-i] < closes[-i - 1] for i in range(1, n + 1))
    except Exception:
        return False


def _normalize_carrier_etf_code(code: str) -> str:
    """6 位 ETF 代码 → SH/SZ 前缀格式（5xxxxx=上交所, 1xxxxx=深交所）。"""
    code = (code or "").strip()
    if code[:2] in ("SH", "SZ", "BJ"):
        return code
    if code[:1] == "5":
        return f"SH{code}"
    if code[:1] == "1":
        return f"SZ{code}"
    return code


def _carrier_switch_note(fund_code: str, as_of: str, cur: str, reason: str = "") -> List[str]:
    """载体切换标注: 相邻交易日载体不同才标注（切换日不清仓, 仅新增资金按新载体）。"""
    prev = _carrier_state.get(fund_code)
    _carrier_state[fund_code] = {"carrier": cur, "as_of": as_of}
    if prev and prev.get("as_of") != as_of and prev.get("carrier") != cur:
        return [f"载体切换：{prev['carrier']} → {cur}" + (f"（{reason}）" if reason else "")]
    return []


def _carrier_active(fund_code: str) -> Optional[Dict[str, Any]]:
    """返回生效的 DCA 载体配置（灰度开启且模式为 fixed_combo/broad），否则 None。"""
    s_cfg = _sector.get_sector_config()
    if not s_cfg.get("dca_carrier_enabled"):
        return None
    carrier = s_cfg.get("dca_carriers", {}).get(fund_code, {})
    if carrier.get("mode") in ("fixed_combo", "broad"):
        return carrier
    return None


def _build_buy_legs(
    fund_code: str,
    indices: List[Dict],
    daily_amount: float,
    as_of: str,
    etf_code: str,
) -> Tuple[List[Tuple[str, str, float]], List[str], str]:
    """坑内仓位拆分。

    guide_only 宽基(板块拆分启用): 全部资金 → 选中板块 ETF 组合（宽基本身不下单）；
    组合为空时返回 empty_reason（调用方跳过当日买入）。
    其他宽基: 保留 PIT_POSITION_SPLIT 90/5/5 拆分（增强标的不满足时回退指数自身）。

    Returns:
        (legs, fallback_notes, empty_reason) — empty_reason 非空表示无板块可买。
    """
    s_cfg = _sector.get_sector_config()
    # ── 载体解析: regime 驱动（regime_carrier_enabled=true）优先, 否则 5.4 静态优先级 ──
    resolved_carrier = _sector.resolve_carrier(fund_code, s_cfg)
    carrier = resolved_carrier if resolved_carrier else _carrier_active(fund_code)
    carrier_mode = carrier.get("mode") if carrier else None
    is_regime_carrier = bool(carrier.get("reason")) if carrier else False

    # ── 静态载体（broad / fixed_combo）直接构建腿 ──
    if carrier_mode in ("broad", "fixed_combo"):
        notes: List[str] = [f"载体={carrier_mode}（{carrier['reason']}）"] if is_regime_carrier else []
        if carrier_mode == "broad":
            key = "carrier:broad" if not is_regime_carrier else "index"
            notes += _carrier_switch_note(fund_code, as_of, "broad", carrier.get("reason", "")) if is_regime_carrier else []
            return [(key, etf_code, daily_amount)], notes, ""
        legs = []
        for c in carrier.get("codes", []):
            code = c.get("code", "")
            weight = float(c.get("weight", 0.0) or 0.0)
            amount = daily_amount * weight
            if amount <= 0:
                continue
            legs.append((f"carrier:{code}", _normalize_carrier_etf_code(code), amount))
        if not legs:
            return [("index", etf_code, daily_amount)], notes + ["fixed_combo 无有效标的, 回退宽基"], ""
        notes += _carrier_switch_note(fund_code, as_of, "fixed_combo", carrier.get("reason", "")) if is_regime_carrier else []
        return legs, notes, ""

    # ── 动态选筹（guide_only + 板块拆分启用; regime 载体 sector_selection 或 5.4 回退路径）──
    if CHINA_INDICES.get(fund_code, {}).get("guide_only") and s_cfg.get("enabled"):
        regime_mode, regime_reason = _sector.resolve_regime_mode(s_cfg)
        if regime_mode == "bh":
            notes = [f"regime=bh 宽基躺平（{regime_reason}）"]
            return [("index", etf_code, daily_amount)], notes, ""
        holdings: List[str] = []
        if s_cfg.get("hold_until_exit"):
            holdings = [
                h.get("etf_code", "") for h in _get_sector_holdings(fund_code)
                if h.get("etf_code")
            ]
        selection = _sector.select_sectors(as_of=as_of, holdings=holdings, mode=regime_mode)
        selected = selection.get("selected", [])
        notes: List[str] = [f"regime={regime_mode}（{regime_reason}）"]
        if selected:
            if _sector_fallback_state.pop(fund_code, None):
                notes.append("板块信号恢复, 切回板块选筹")
            if selection.get("empty_reason"):
                notes.append(selection["empty_reason"])
            notes += _carrier_switch_note(fund_code, as_of, "sector_selection", regime_reason)
            legs = [(s["sector"], s["etf_code"], daily_amount * s["weight"]) for s in selected]
            return legs, notes, ""
        if s_cfg.get("fallback_broad"):
            _sector_fallback_state[fund_code] = as_of
            reason = selection.get("empty_reason", "无信号")
            notes.append(f"选筹失败回退（{reason}）")
            # 三级回退链: 第一级 fixed_combo 高弹性组合 → 第二级 宽基本身
            combo = s_cfg.get("dca_carriers", {}).get(fund_code, {})
            if combo.get("mode") == "fixed_combo" and combo.get("codes"):
                legs = []
                for c in combo["codes"]:
                    code = c.get("code", "")
                    weight = float(c.get("weight", 0.0) or 0.0)
                    amount = daily_amount * weight
                    if amount <= 0:
                        continue
                    legs.append((f"carrier:{code}", _normalize_carrier_etf_code(code), amount))
                if legs:
                    notes.append("回退层级1: fixed_combo 高弹性组合")
                    return legs, notes, ""
                notes.append("fixed_combo 无有效标的, 继续回退")
            notes.append("回退层级2: 宽基")
            return [("index", etf_code, daily_amount)], notes, ""
        return [], [], selection.get("empty_reason", "无信号")
    semi_by_code = {i["fund_code"]: i for i in indices if i.get("tier") == "semi_boost"}
    index_weight = PIT_POSITION_SPLIT.get("index", 1.0)
    legs = [("index", etf_code, daily_amount * index_weight)]
    fallback_notes = []
    for s_code, s_weight in (
        ("588200", PIT_POSITION_SPLIT.get("588200", 0.0)),
        ("512480", PIT_POSITION_SPLIT.get("512480", 0.0)),
    ):
        s_amount = daily_amount * s_weight
        if s_amount <= 0:
            continue
        s_idx = semi_by_code.get(s_code)
        can_buy = s_idx is not None and s_idx.get("status") in ("golden_pit", "warning")
        if not can_buy:
            legs[0] = (legs[0][0], legs[0][1], legs[0][2] + s_amount)
            fallback_notes.append(
                f"{SEMI_BOOST_INDICES.get(s_code, {}).get('name', s_code)}未入坑→回退指数"
            )
            continue
        s_etf = SEMI_BOOST_INDICES.get(s_code, {}).get("etf_code", "")
        if s_etf:
            legs.append((s_code, s_etf, s_amount))
    return legs, fallback_notes, ""


def _get_quote(etf_code: str) -> Optional[Dict[str, Any]]:
    """直接调用 XueqiuEngine 获取实时报价。"""
    try:
        from workspace_detector import XUEQIU_DIR
        sys.path.insert(0, str(XUEQIU_DIR))
        from xueqiu_engine import XueqiuEngine
        engine = XueqiuEngine(config_file=str(XUEQIU_DIR / "config.json"))
        return engine.get_stock_quote(etf_code)
    except Exception as e:
        logger.warning("获取 %s 实时报价失败: %s", etf_code, e)
        return None


def _get_executor():
    """获取 golden_pit 账户交易执行器（直连 PaperTradingEngine，与 stock 账户隔离）。"""
    from app.core.trading.marcus_trade import MarcusVNPyExecutor
    return MarcusVNPyExecutor(account_id="golden_pit")


def _place_buy_order(etf_code: str, amount: float, reason: str) -> Tuple[bool, str]:
    """直接调用 MarcusVNPyExecutor 下限价买入单（限价 × 1.02 以保证成交）。"""
    try:
        quote = _get_quote(etf_code)
        if not quote:
            return False, "无法获取当前价格"
        current_price = quote.get("current") or quote.get("last_close")
        if not current_price or current_price <= 0:
            return False, "无法获取当前价格"

        shares = int(amount / current_price / 100) * 100
        if shares < 100:
            return False, f"金额不足: {amount:.0f} < 1手({current_price * 100:.0f})"

        limit_price = round(current_price * 1.02, 2)
        executor = _get_executor()
        result = executor.buy(symbol=etf_code, price=limit_price, volume=shares, reason=reason)

        if result.get("status") == "executed":
            order_id = result.get("order_id", "")
            logger.info("黄金坑 DCA 买入 %s: %d股 @%.2f, order=%s", etf_code, shares, limit_price, order_id)
            return True, order_id
        else:
            msg = result.get("reason", result.get("status", "unknown"))
            logger.warning("黄金坑 DCA 买入 %s 失败: %s", etf_code, msg)
            return False, msg
    except Exception as e:
        logger.error("黄金坑 DCA 买入 %s 异常: %s", etf_code, e)
        return False, str(e)


def _place_sell_order(etf_code: str, shares: int, reason: str) -> Tuple[bool, str]:
    """直接调用 MarcusVNPyExecutor 下限价卖单（限价 × 0.98 以保证成交）。"""
    try:
        quote = _get_quote(etf_code)
        if not quote:
            return False, "无法获取当前价格"
        current_price = quote.get("current") or quote.get("last_close")
        if not current_price or current_price <= 0:
            return False, "无法获取当前价格"

        if shares < 100:
            return False, f"股数不足: {shares} < 100"

        limit_price = round(current_price * 0.98, 2)
        executor = _get_executor()
        result = executor.sell(symbol=etf_code, price=limit_price, volume=shares, reason=reason)

        if result.get("status") == "executed":
            order_id = result.get("order_id", "")
            logger.info("黄金坑 DCA 卖出 %s: %d股 @%.2f, order=%s", etf_code, shares, limit_price, order_id)
            return True, order_id
        else:
            msg = result.get("reason", result.get("status", "unknown"))
            logger.warning("黄金坑 DCA 卖出 %s 失败: %s", etf_code, msg)
            return False, msg
    except Exception as e:
        logger.error("黄金坑 DCA 卖出 %s 异常: %s", etf_code, e)
        return False, str(e)


def _amount_to_sell_shares(etf_code: str, amount: float) -> int:
    """按现价把退出金额换算为 100 股整数倍（不足一手返回 0）。"""
    quote = _get_quote(etf_code)
    if not quote:
        return 0
    price = quote.get("current") or quote.get("last_close")
    if not price or price <= 0:
        return 0
    return int(amount / price / 100) * 100


def _execute_exit_sell(
    fund_code: str,
    window_start: str,
    buy_day: int,
    etf_code: str,
    amount: float,
    strategy: str,
    shares: int,
    schedule_day: int,
) -> Tuple[bool, str]:
    """执行退出卖单并落盘（filled/failed），返回 (成功?, order_id/失败原因)。"""
    if shares < 100:
        _record_dca_log(
            fund_code=fund_code, window_start=window_start,
            buy_day=buy_day, etf_code=etf_code,
            amount=round(amount, 2), strategy=strategy,
            order_id="", status="failed",
            schedule_day=schedule_day, trend_factor=0.0,
        )
        return False, f"金额不足一手: {shares}股"
    ok, order_info = _place_sell_order(etf_code, shares, strategy)
    if ok:
        _record_dca_log(
            fund_code=fund_code, window_start=window_start,
            buy_day=buy_day, etf_code=etf_code,
            amount=round(amount, 2), strategy=strategy,
            order_id=order_info, status="filled",
            schedule_day=schedule_day, trend_factor=0.0,
        )
        return True, order_info
    _record_dca_log(
        fund_code=fund_code, window_start=window_start,
        buy_day=buy_day, etf_code=etf_code,
        amount=round(amount, 2), strategy=strategy,
        order_id="", status="failed",
        schedule_day=schedule_day, trend_factor=0.0,
    )
    return False, order_info


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
                .filter(
                    PaperPosition.account_id == 'golden_pit',
                    PaperPosition.symbol == etf_code,
                )
                .first()
            )
            if not pos:
                code = re.sub(r'^(SH|SZ|BJ)', '', etf_code)
                pos = (
                    db.query(PaperPosition)
                    .filter(
                        PaperPosition.account_id == 'golden_pit',
                        PaperPosition.symbol == code,
                    )
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
    schedule_day: int = None,
    trend_factor: float = None,
):
    """记录 DCA 执行日志到数据库 (v5: 新增 schedule_day/trend_factor)。"""
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        if not window_start:
            window_start = datetime.now().strftime("%Y-%m-%d")
            logger.warning("DCA 日志: window_start 为空, 回退为今天 %s", window_start)

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
                schedule_day=schedule_day,
                trend_factor=trend_factor,
            )
            db.add(log)
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.warning("记录 DCA 日志失败: %s", e)


def _sell_defense_on_reentry(source_code: str, today_str: str) -> List[str]:
    """宽基重新入场时，卖出其撤场时轮入的防御持仓（永久持有模式，仅通知/记账）。

    防重复：防御承接批次以 (d_code, window_start) 标识；卖出记录
    strategy="exit/defense_reentry/{d_code}" 且 window_start 与批次一致，已卖出批次自动跳过。
    旧格式 defense_rotation/{d_code}（无来源）无法关联，保留历史记录不处理。
    """
    try:
        from app.database import SessionLocal
        from app.models.golden_pit_dca_log import GoldenPitDCALog

        db = SessionLocal()
        try:
            rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.strategy.like(f"defense_rotation/{source_code}/%"),
                    GoldenPitDCALog.status.in_(("filled", "notified")),
                    GoldenPitDCALog.amount > 0,
                )
                .all()
            )
            sold_rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.strategy.like("exit/defense_reentry/%"),
                    GoldenPitDCALog.status.in_(("filled", "notified")),
                )
                .all()
            )
            today_rows = (
                db.query(GoldenPitDCALog)
                .filter(
                    GoldenPitDCALog.strategy.like("exit/defense_reentry/%"),
                )
                .all()
            )
        finally:
            db.close()
    except Exception as e:
        logger.warning("查询防御承接持仓失败 (%s): %s", source_code, e)
        return []

    sold_keys = {(r.fund_code, r.window_start or "") for r in sold_rows}
    # 当日已尝试卖出（含 failed）不再重试，次日再评估
    today_attempted = {
        (r.fund_code, r.window_start or "")
        for r in today_rows
        if (r.created_at or "").startswith(today_str)
    }
    batches: Dict[Tuple[str, str], List[Any]] = {}
    for r in rows:
        parts = (r.strategy or "").split("/")
        if len(parts) < 3:
            continue  # 旧格式无来源，跳过
        d_code = parts[2]
        key = (d_code, r.window_start or "")
        if key in sold_keys or key in today_attempted:
            continue
        b = batches.setdefault(key, [r.etf_code, 0.0])
        b[1] += r.amount

    msgs = []
    for (d_code, ws), (d_etf, amount) in batches.items():
        if amount <= 0 or not d_etf:
            continue
        shares = _amount_to_sell_shares(d_etf, amount)
        ok, order_info = _execute_exit_sell(
            fund_code=d_code,
            window_start=ws or today_str,
            buy_day=0,
            etf_code=d_etf,
            amount=amount,
            strategy=f"exit/defense_reentry/{d_code}",
            shares=shares,
            schedule_day=0,
        )
        d_name = DEFENSE_INDICES.get(d_code, {}).get("name", d_code)
        if ok:
            msgs.append(f"🛡 防御取出: {d_name}({d_etf}) 卖出 ¥{amount:.0f}（{source_code} 重新入场，赎回资金回补）(order: {order_info})")
        else:
            msgs.append(f"❌ 防御取出未成交: {d_name}({d_etf}) ¥{amount:.0f}（{order_info}）")
    return msgs


def _get_holdings_detail() -> List[Dict[str, Any]]:
    """查询所有黄金坑 ETF 的持仓明细（FIFO 成本 + 实时价格 + 盈亏）。"""
    from app.database import SessionLocal
    from app.models.paper_trade import PaperTrade, PaperPosition
    from app.services.golden_pit_config import ALL_INDEX_CONFIGS
    from sqlalchemy import func

    db = SessionLocal()
    try:
        # 构建 ETF code → (index_name, tier, fund_code) 映射
        etf_configs = _get_etf_configs()
        etf_map: Dict[str, Dict] = {}  # etf_code → {index_name, tier, fund_code}
        gp_symbols: set = set()
        for cfg in etf_configs:
            code = cfg["fund_code"]
            if code not in ALL_INDEX_CONFIGS:
                continue
            ec = cfg["etf_code"]
            ec_no = re.sub(r'^(SH|SZ|BJ)', '', ec)
            info = {
                "index_name": ALL_INDEX_CONFIGS[code]["name"],
                "tier": ALL_INDEX_CONFIGS[code].get("tier", ""),
                "fund_code": code,
            }
            etf_map[ec] = info
            etf_map[ec_no] = info
            gp_symbols.add(ec)
            gp_symbols.add(ec_no)

        pos_rows = (
            db.query(PaperPosition)
            .filter(PaperPosition.account_id == 'golden_pit')
            .all()
        )
        held_symbols = []
        for pos in pos_rows:
            sym = pos.symbol
            sym_no_prefix = re.sub(r'^(SH|SZ|BJ)', '', sym)
            if sym in gp_symbols or sym_no_prefix in gp_symbols:
                held_symbols.append(sym)

        if not held_symbols:
            return []

        # FIFO 重放计算持仓量+成本
        trades = (
            db.query(PaperTrade)
            .filter(
                PaperTrade.account_id == 'golden_pit',
                PaperTrade.symbol.in_(held_symbols),
                (PaperTrade.voided == 0) | (PaperTrade.voided == None),
            )
            .order_by(
                func.coalesce(PaperTrade.trade_date, func.substr(PaperTrade.created_at, 1, 10)),
                PaperTrade.id,
            )
            .all()
        )

        # 按 symbol 组织 FIFO lots
        lots_map: Dict[str, List[Dict]] = {}
        for t in trades:
            sym = t.symbol
            if t.direction == '买入':
                entry_date = t.trade_date or (t.created_at[:10] if t.created_at else '')
                lots_map.setdefault(sym, []).append({
                    'price': t.price, 'volume': t.volume, 'entry_date': entry_date
                })
            elif t.direction == '卖出':
                lots = lots_map.get(sym, [])
                remaining = t.volume
                i = 0
                while remaining > 0 and i < len(lots):
                    used = min(lots[i]['volume'], remaining)
                    lots[i]['volume'] -= used
                    remaining -= used
                    if lots[i]['volume'] == 0:
                        lots.pop(i)
                    else:
                        i += 1

        # 构建持仓详情
        holdings = []
        for sym in held_symbols:
            lots = lots_map.get(sym, [])
            if not lots:
                continue
            total_vol = sum(l['volume'] for l in lots)
            if total_vol < 100:
                continue
            avg_cost = sum(l['price'] * l['volume'] for l in lots) / total_vol
            entry_dates = [l['entry_date'] for l in lots if l.get('entry_date')]
            first_entry = min(entry_dates) if entry_dates else ''

            # 获取实时价格
            quote = _get_quote(sym)
            current_price = None
            change_pct = 0.0
            if quote:
                current_price = quote.get("current") or quote.get("last_close")
                change_pct = quote.get("change_pct", 0.0)

            if not current_price or current_price <= 0:
                continue

            market_value = total_vol * current_price
            cost_value = total_vol * avg_cost
            float_pnl = market_value - cost_value
            float_pnl_pct = (current_price / avg_cost - 1) * 100 if avg_cost > 0 else 0

            # 匹配指数名称
            matched = etf_map.get(sym) or etf_map.get(re.sub(r'^(SH|SZ|BJ)', '', sym)) or {}
            index_name = matched.get("index_name", "")
            fund_code = matched.get("fund_code", "")
            tier = matched.get("tier", "")

            # 计算持仓天数
            days_held = 0
            if first_entry:
                try:
                    entry_dt = datetime.strptime(first_entry, "%Y-%m-%d")
                    days_held = (datetime.now() - entry_dt).days
                except ValueError:
                    pass

            holdings.append({
                "index_name": index_name or sym,
                "fund_code": fund_code,
                "etf_code": sym,
                "shares": total_vol,
                "avg_cost": round(avg_cost, 3),
                "current_price": round(current_price, 3),
                "market_value": round(market_value, 2),
                "float_pnl": round(float_pnl, 2),
                "float_pnl_pct": round(float_pnl_pct, 2),
                "change_pct": round(change_pct, 2),
                "entry_date": first_entry,
                "days_held": days_held,
                "tier": tier,
            })

        # 按 tier 排序: core > satellite > defense > 半导体增强 > 防御轮动
        tier_order = {"core": 0, "satellite": 1, "defense": 2,
                      "semi_boost": 3, "defense_rotation": 4}
        holdings.sort(key=lambda h: (tier_order.get(h["tier"], 9), h["index_name"]))
        return holdings

    except Exception as e:
        logger.warning("获取持仓明细失败: %s", e)
        return []
    finally:
        db.close()


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


def execute_golden_pit_dca(time_slot: Optional[str] = None) -> Dict[str, Any]:
    """
    黄金坑 DCA 定投主逻辑 v5 — DCA基准权重 × 趋势调节因子。

    阶段:
      waiting — 黄金坑信号活跃但拐点未确认 → DCA基准×趋势减速因子
      buying  — 拐点确认 → DCA基准×趋势加速因子

    仓位逻辑:
      daily_amount = max_total × dca_weight[day] × trend_factor × pos_mult × resonance × macro_coef

      DCA 基准权重 (来自回测, 分指数):
        lump_entry = [100%, 0, 0, ...]
        uniform_3  = [33%, 33%, 33%, 0, ...]

      趋势调节因子 (根据实时趋势状态):
        declining(0.1x) → bottoming(0.5x) → turning(1.0x) → accelerating(1.2x) → full(1.5x)

      安全制动:
        假信号 (greed ≥ entry_greed) → 暂停, 标记 aborted
        飞刀保护 (单日跌幅 > 2pp) → 跳过当日
        累计硬截断 (total ≥ max_total) → 停止

    跳过 drop(放弃) 和 watch(仅观察) 级别的指数。

    Args:
        time_slot: 时间批次 ("morning" / "afternoon"), None 表示不筛选 (向后兼容)

    Returns:
        结构化 dict，包含 holdings/exit_signals/buy_candidates/skipped/summary_text 等字段。
    """
    from app.services.golden_pit_service import get_golden_pit_service, CHINA_INDICES, get_trend_factor

    # 1. 获取黄金坑状态
    try:
        gp_service = get_golden_pit_service()
        status = gp_service.get_status()
    except Exception as e:
        msg = f"获取黄金坑状态失败: {e}"
        logger.error(msg)
        return msg

    window = status.get("golden_pit_window", {})
    indices = status.get("indices", [])
    as_of = status.get("as_of", "")

    phase = window.get("phase", "idle")
    current_day = window.get("current_day", 0)
    window_start = window.get("start_date", "")
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 全球宏观数据
    global_macro = status.get("global_macro", {})
    macro_coef = global_macro.get("global_macro_coefficient", 1.0)
    liquidity_gate = global_macro.get("liquidity_gate", "open")

    if phase == "idle":
        ind_track = _run_industry_track(as_of, today_str, gate_open=(liquidity_gate != "closed"))
        summary = f"[{as_of}] 无黄金坑信号，跳过指数级 DCA"
        if ind_track["lines"]:
            summary += "\n" + "\n".join(ind_track["lines"])
        return {
            "as_of": as_of,
            "phase": "idle",
            "phase_label": "无信号",
            "summary_text": summary,
            "holdings": _get_holdings_detail(),
            "exit_signals": [],
            "buy_candidates": [],
            "skipped": [],
            "stats": {},
            "industry_monitor": ind_track["monitor"],
        }

    logger.info(
        "黄金坑 DCA: phase=%s day=%d, start=%s, leading=%s(%s), pit=%d, warning=%d, turning=%d",
        phase, current_day, window_start,
        window.get("leading_index"), window.get("leading_tier", ""),
        window.get("pit_count", 0), window.get("warning_count", 0),
        window.get("turning_count", 0),
    )

    # ── 流动性闸门硬停止 ──
    if liquidity_gate == "closed":
        gate_msg = (
            f"黄金坑 DCA v4 (趋势驱动 · 仅通知) — {today_str}\n"
            f"🔒 全球流动性闸门关闭 (sentiment_score={global_macro.get('sentiment_score', 'N/A')})\n"
            f"   原因: {global_macro.get('summary', '')}\n"
            f"   所有买入已跳过，等待闸门重新开启。"
        )
        logger.warning("黄金坑 DCA: 全球流动性闸门关闭，跳过所有买入")
        ind_track = _run_industry_track(as_of, today_str, gate_open=False)
        if ind_track["lines"]:
            gate_msg += "\n" + "\n".join(ind_track["lines"])
        return {
            "as_of": as_of,
            "phase": phase,
            "phase_label": "买入窗口",
            "summary_text": gate_msg,
            "holdings": _get_holdings_detail(),
            "exit_signals": [],
            "buy_candidates": [],
            "skipped": [],
            "stats": {"liquidity_gate": "closed", "macro_coef": macro_coef},
            "industry_monitor": ind_track["monitor"],
        }

    # 2. 读取 ETF 配置
    etf_configs = _get_etf_configs()
    if not etf_configs:
        ind_track = _run_industry_track(as_of or today_str, today_str, gate_open=(liquidity_gate != "closed"))
        summary = "无启用的黄金坑 ETF 配置"
        if ind_track["lines"]:
            summary += "\n" + "\n".join(ind_track["lines"])
        return {
            "as_of": as_of,
            "phase": phase,
            "phase_label": "买入窗口",
            "summary_text": summary,
            "holdings": _get_holdings_detail(),
            "exit_signals": [],
            "buy_candidates": [],
            "skipped": [],
            "stats": {},
            "industry_monitor": ind_track["monitor"],
        }

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

    # ── 分时过滤: time_slot 非空时仅保留 buy_time 匹配当前批次的指数 ──
    if time_slot:
        tradeable = [
            i for i in tradeable
            if _time_matches_slot(
                _get_buy_time(i["fund_code"], i.get("days_in_pit") or 0), time_slot
            )
        ]

    results = []
    executed_count = 0
    skipped_count = 0
    total_invested_today = 0.0

    # ── 退出信号检查: 对已持仓(真实/模拟)ETF 检查是否需要卖出(仅通知) ──
    for idx in indices:
        exit_signal = idx.get("exit_signal")
        if not exit_signal:
            continue
        fund_code = idx["fund_code"]
        cfg = config_by_fund.get(fund_code)
        if not cfg:
            continue
        # 防御轮动为永久持有模式：不因自身 P 分位单独止盈，持有至宽基重新入场时卖出
        if idx.get("tier") == "defense_rotation":
            continue
        # guide_only 宽基: 退出信号 → 组合级清仓对应板块 ETF（不对宽基本身下单）
        if CHINA_INDICES.get(fund_code, {}).get("guide_only") and _sector.get_sector_config().get("enabled"):
            sector_holdings = _get_sector_holdings(fund_code)
            if not sector_holdings:
                continue
            if _has_exit_notice(fund_code):
                continue
            sell_ratio = 1.0 if exit_signal in ("full_exit", "stop_profit", "fallback_exit") else (
                0.5 if exit_signal == "half_exit" else 0.0
            )
            if sell_ratio <= 0:
                continue
            freed_capital = 0.0
            exit_filled = []
            exit_failed = []
            for sh in sector_holdings:
                sell_amount = sh["amount"] * sell_ratio
                if sell_amount < 100:
                    continue
                shares = _amount_to_sell_shares(sh["etf_code"], sell_amount)
                ok, order_info = _execute_exit_sell(
                    fund_code=fund_code, window_start=window_start,
                    buy_day=current_day, etf_code=sh["etf_code"],
                    amount=sell_amount, strategy=f"exit/{exit_signal}/sector",
                    shares=shares, schedule_day=current_day,
                )
                if ok:
                    freed_capital += sell_amount
                    exit_filled.append((sh["etf_code"], order_info))
                else:
                    exit_failed.append(f"{sh['etf_code']}（{order_info}）")
            exit_icon = {"half_exit": "🟡", "full_exit": "🔴", "stop_profit": "🟢", "fallback_exit": "🔔"}.get(exit_signal, "")
            if exit_failed:
                results.append(
                    f"❌ 退出信号 {idx['index_name']} (组合级) 卖单未成交: " + "；".join(exit_failed)
                )
            if exit_filled:
                order_text = "；".join(f"{c}#{o}" for c, o in exit_filled)
                results.append(
                    f"{exit_icon} 退出信号 {idx['index_name']} (组合级): "
                    f"已卖出板块 ETF ¥{freed_capital:.0f} [{exit_signal}] (order: {order_text})"
                )
            if sell_ratio >= 1.0 and freed_capital > 0:
                for d_code, d_weight in DEFENSE_TAKEOVER_WEIGHTS.items():
                    d_cfg = DEFENSE_INDICES.get(d_code, {})
                    d_etf = d_cfg.get("etf_code", "")
                    if not d_etf:
                        continue
                    _record_dca_log(
                        fund_code=d_code, window_start=today_str,
                        buy_day=0, etf_code=d_etf,
                        amount=round(freed_capital * d_weight, 2),
                        strategy=f"defense_rotation/{fund_code}/{d_code}",
                        order_id="", status="notified",
                        schedule_day=0, trend_factor=0.0,
                    )
                defense_names = "/".join(
                    DEFENSE_INDICES.get(c, {}).get("name", c) for c in DEFENSE_TAKEOVER_WEIGHTS
                )
                results.append(
                    f"🛡 防御承接: {idx['index_name']} 撤场资金 ¥{freed_capital:.0f} "
                    f"→ {defense_names} 等权（持有至宽基重新入场）"
                )
            continue
        etf_code = cfg["etf_code"]
        holding = _get_holding_shares(etf_code)
        sim_amount = _get_simulated_position_amount(etf_code)
        if holding < 100 and sim_amount < 100:
            continue
        if _has_exit_notice(fund_code):
            continue

        quote = _get_quote(etf_code)
        price = None
        if quote:
            price = quote.get("current") or quote.get("last_close")
        if not price or price <= 0:
            continue

        sim_shares = int(sim_amount / price / 100) * 100
        total_shares = max(holding, sim_shares)

        if exit_signal in ("full_exit", "stop_profit", "fallback_exit"):
            sell_shares = total_shares
            sell_amount = sim_amount
        elif exit_signal == "half_exit":
            sell_shares = int(total_shares / 2 / 100) * 100
            sell_amount = sim_amount * 0.5
        else:
            continue

        if sell_shares < 100:
            continue

        # 真实落单: 卖出 golden_pit 账户持仓，成功记 filled，失败降级为通知
        ok, order_info = _execute_exit_sell(
            fund_code=fund_code,
            window_start=window_start,
            buy_day=current_day,
            etf_code=etf_code,
            amount=sell_amount,
            strategy=f"exit/{exit_signal}",
            shares=sell_shares,
            schedule_day=current_day,
        )

        exit_icon = {"half_exit": "🟡", "full_exit": "🔴", "stop_profit": "🟢", "fallback_exit": "🔔"}.get(exit_signal, "")
        if ok:
            results.append(
                f"{exit_icon} 退出信号 {idx['index_name']} {etf_code}: "
                f"已卖出 {sell_shares}股 约¥{sell_amount:.0f} [{exit_signal}] (order: {order_info})"
            )
        else:
            results.append(
                f"❌ 退出信号 {idx['index_name']} {etf_code}: "
                f"卖出未成交 ({order_info})"
            )

        # ── 撤场后防御承接: 卖出成功后资金按防御组合等权配置(仅记录) ──
        if ok and exit_signal in ("full_exit", "stop_profit", "fallback_exit"):
            freed_capital = sell_amount if sell_amount > 0 else sell_shares * price
            if freed_capital > 0:
                for d_code, d_weight in DEFENSE_TAKEOVER_WEIGHTS.items():
                    d_cfg = DEFENSE_INDICES.get(d_code, {})
                    d_etf = d_cfg.get("etf_code", "")
                    if not d_etf:
                        continue
                    _record_dca_log(
                        fund_code=d_code,
                        window_start=today_str,
                        buy_day=0,
                        etf_code=d_etf,
                        amount=freed_capital * d_weight,
                        strategy=f"defense_rotation/{fund_code}/{d_code}",
                        order_id="",
                        status="notified",
                        schedule_day=0, trend_factor=0.0,
                    )
                defense_names = "/".join(
                    DEFENSE_INDICES.get(c, {}).get("name", c) for c in DEFENSE_TAKEOVER_WEIGHTS
                )
                results.append(
                    f"🛡 防御承接: {idx['index_name']} 撤场资金 ¥{freed_capital:.0f} "
                    f"→ {defense_names} 等权（持有至宽基重新入场）"
                )

    # ── 板块 ETF 独立二次拐点退出（价格驱动, 仅通知）──
    # fixed_combo/broad 载体按宽基窗口退出，不启用板块连跌（与回测口径一致）
    if _sector.get_sector_config().get("enabled"):
        for idx in [i for i in indices if i.get("guide_only")]:
            if _carrier_active(idx["fund_code"]):
                continue
            if _has_exit_notice(idx["fund_code"]):
                continue
            for sh in _get_sector_holdings(idx["fund_code"]):
                if sh.get("carrier"):
                    # fixed_combo(carrier) 腿按宽基窗口退出, 不做板块连跌（与回测口径一致）
                    continue
                if _check_sector_down_turn(sh["etf_code"]):
                    shares = _amount_to_sell_shares(sh["etf_code"], sh["amount"])
                    ok, order_info = _execute_exit_sell(
                        fund_code=idx["fund_code"], window_start=window_start,
                        buy_day=current_day, etf_code=sh["etf_code"],
                        amount=sh["amount"], strategy=f"exit/down_turn/{sh['etf_code']}",
                        shares=shares, schedule_day=current_day,
                    )
                    if ok:
                        exit_days = int(
                            _sector.get_sector_params(sh["etf_code"]).get("exit_down_days")
                            or _sector.get_sector_config().get("exit_down_days", SECTOR_EXIT_DOWN_DAYS)
                        )
                        results.append(
                            f"🔻 板块二次拐点 {sh['etf_code']}: 连续{exit_days}天回落, "
                            f"已清仓 ¥{sh['amount']:.0f} (order: {order_info})"
                        )
                    else:
                        results.append(
                            f"❌ 板块二次拐点 {sh['etf_code']}: "
                            f"卖出未成交 ({order_info})"
                        )

    for idx in tradeable:
        fund_code = idx["fund_code"]
        cfg = config_by_fund.get(fund_code)
        if not cfg:
            continue

        buy_time = _get_buy_time(fund_code, idx.get("days_in_pit") or 0)

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

        # ── 永久持有: 宽基重新入场 → 卖出其撤场时轮入的防御持仓(仅通知/记账) ──
        reentry_msgs = _sell_defense_on_reentry(fund_code, today_str)
        if reentry_msgs:
            results.extend(reentry_msgs)

        # ── 仓位计算 v5: DCA基准权重 × 趋势调节因子 ──
        max_total = cfg["max_total_amount"]
        turning_confirmed = idx.get("turning_point_confirmed", False)
        days_rising = idx.get("days_rising", 0)
        trend = idx.get("trend", "declining")

        index_params = get_effective_index_config(fund_code)
        pos_mult = index_params.get("position_multiplier", 1.0)
        dca_strategy = index_params.get("dca_strategy", "uniform_10")
        dca_fallback = index_params.get("dca_fallback", 10)
        entry_greed = index_params.get("entry_greed", 0.50)
        current_greed = idx.get("greed", 0.0)

        schedule_day = current_day  # 窗口内第几天, 对应 DCA 权重索引

        # DCA 基准权重: 回测优化的固定时间表
        dca_weights = _strategy_weights(dca_strategy)
        dca_weight = dca_weights[min(schedule_day, PIT_WINDOW_DAYS - 1)]

        # 累计已投
        total_invested = sum(
            _get_day_amount(fund_code, window_start, d) for d in executed_days
        )
        remaining = max_total - total_invested
        if remaining <= 0:
            logger.info("%s: 累计已达上限 %.0f，跳过", fund_code, max_total)
            skipped_count += 1
            continue

        # DCA 窗口超时兜底: 超过 fallback 天数且还有剩余额度 → 强制完成
        if dca_weight == 0.0 and schedule_day >= dca_fallback and remaining > 0:
            dca_active_days = sum(1 for w in dca_weights if w > 0)
            remaining_slots = max(dca_active_days - len(executed_days), 1)
            dca_weight = min(remaining / max_total / remaining_slots, 1.0)
            logger.info("%s: DCA窗口超时(day%d>fallback%d), 兜底权重=%.2f",
                        fund_code, schedule_day, dca_fallback, dca_weight)
        elif dca_weight == 0.0:
            logger.info("%s: DCA权重为0(day%d), 等待窗口推进或兜底触发",
                        fund_code, schedule_day)
            skipped_count += 1
            continue

        # 趋势调节因子: 替代硬编码的 POSITION_TIERS
        trend_factor = get_trend_factor(
            trend=trend, days_rising=days_rising,
            fund_code=fund_code,
            current_greed=current_greed, entry_greed=entry_greed,
        )

        # ── 二次信号检测: 贪婪创新低 → 重置 DCA 窗口 ──
        signal_trigger_greed = idx.get("signal_trigger_greed")
        if (signal_trigger_greed is not None and signal_trigger_greed > 0
                and current_greed < signal_trigger_greed * 0.95
                and schedule_day > 0):
            # 检查是否已重置过 (最多1次)
            has_reset = _check_window_reset_count(fund_code, window_start) > 0
            if not has_reset:
                logger.info("%s: 二次信号检测 greed=%.4f < trigger=%.4f ×0.95, 重置schedule_day=0",
                            fund_code, current_greed, signal_trigger_greed)
                _record_dca_log(
                    fund_code=fund_code, window_start=window_start,
                    buy_day=current_day, etf_code=cfg["etf_code"],
                    amount=0, strategy=_encode_strategy(dca_strategy, trend, trend_factor, "window_reset", buy_time),
                    order_id="", status="safety_brake",
                    schedule_day=schedule_day, trend_factor=trend_factor,
                )
                schedule_day = 0
                dca_weights = _strategy_weights(dca_strategy)
                dca_weight = dca_weights[0]

        # ── 安全制动 1: 假信号检测 ──
        if entry_greed > 0 and current_greed >= entry_greed:
            logger.info("%s: 假信号暂停 (greed=%.4f >= entry_greed=%.4f)",
                        fund_code, current_greed, entry_greed)
            _record_dca_log(
                fund_code=fund_code, window_start=window_start,
                buy_day=current_day, etf_code=cfg["etf_code"],
                amount=0, strategy=_encode_strategy(dca_strategy, trend, trend_factor, "fake_signal", buy_time),
                order_id="", status="aborted",
                schedule_day=schedule_day, trend_factor=trend_factor,
            )
            skipped_count += 1
            continue

        # ── 安全制动 2: 飞刀保护 (单日跌幅>2个百分点) ──
        prev_greed = _get_prev_greed(fund_code, indices)
        if prev_greed is not None and (prev_greed - current_greed) > 0.02:
            logger.info("%s: 飞刀保护 (greed跌幅=%.4f > 2pp), 跳过当日买入",
                        fund_code, prev_greed - current_greed)
            _record_dca_log(
                fund_code=fund_code, window_start=window_start,
                buy_day=current_day, etf_code=cfg["etf_code"],
                amount=0, strategy=_encode_strategy(dca_strategy, trend, trend_factor, "falling_knife", buy_time),
                order_id="", status="safety_brake",
                schedule_day=schedule_day, trend_factor=trend_factor,
            )
            skipped_count += 1
            continue

        # ── 安全制动 3: 一次性打入反转保护 (仅 lump_entry 策略) ──
        should_reverse, reversal_reason = _check_lump_reversal(
            fund_code=fund_code, schedule_day=schedule_day,
            dca_strategy=dca_strategy, window_start=window_start,
            current_greed=current_greed,
        )
        if should_reverse:
            logger.info("%s: lump_entry 反转保护触发, 切换为 uniform_5", fund_code)
            dca_strategy = "uniform_5"
            dca_weights = _strategy_weights(dca_strategy)
            dca_weight = dca_weights[min(schedule_day, PIT_WINDOW_DAYS - 1)]
            _record_dca_log(
                fund_code=fund_code, window_start=window_start,
                buy_day=current_day, etf_code=cfg["etf_code"],
                amount=0,
                strategy=_encode_strategy("uniform_5", trend, trend_factor, f"lump_reversal/{reversal_reason}", buy_time),
                order_id="", status="safety_brake",
                schedule_day=schedule_day, trend_factor=trend_factor,
            )
            # 切换后如果当天权重为0, 继续正常执行(后续天会按 uniform_5 走)
            if dca_weight == 0.0:
                logger.info("%s: 反转后 uniform_5 day%d 权重=0, 等待后续执行", fund_code, schedule_day)
                skipped_count += 1
                continue

        # 仓位叠加: max_total × dca_weight × trend_factor × pos_mult × resonance × macro_coef
        resonance = _resonance_multiplier(indices)
        daily_amount = max_total * dca_weight * trend_factor * pos_mult * resonance * macro_coef

        # ── 安全制动 3: 累计硬截断 ──
        daily_amount = min(daily_amount, remaining)

        # 趋势状态标签(用于日志和展示)
        position_tier = _trend_state_label(days_rising)

        etf_code = cfg["etf_code"]
        reason = (
            f"[黄金坑DCA v5] {idx['index_name']} "
            f"dca={dca_strategy}(w{dca_weight:.2f}) trend={trend} f={trend_factor:.1f}x "
            f"pos_mult={pos_mult:.1f}x resonance={resonance:.1f}x macro={macro_coef:.1f}x "
            f"day{schedule_day}/{dca_fallback} greed={current_greed:.4f}"
        )

        logger.info(
            "黄金坑 DCA: %s day=%d dca=%s(w%.2f) trend=%s(f%.1f) amount=%.0f resonance=%.1fx macro=%.1fx",
            etf_code, schedule_day, dca_strategy, dca_weight, trend, trend_factor,
            daily_amount, resonance, macro_coef,
        )

        # ── 坑内仓位拆分（_build_buy_legs: guide_only→板块ETF / 其他→90/5/5）──
        legs, fallback_notes, empty_reason = _build_buy_legs(
            fund_code, indices, daily_amount, as_of, etf_code
        )
        if empty_reason:
            _record_dca_log(
                fund_code=fund_code, window_start=window_start,
                buy_day=current_day, etf_code=cfg["etf_code"],
                amount=0,
                strategy=_encode_strategy(dca_strategy, trend, trend_factor, "sector_empty", buy_time),
                order_id="", status="safety_brake",
                schedule_day=schedule_day, trend_factor=trend_factor,
            )
            skipped_count += 1
            results.append(
                f"🧭 {idx['index_name']}: 板块选筹为空, 跳过当日买入 ({empty_reason})"
            )
            continue

        for leg_key, leg_etf, leg_amount in legs:
            if leg_amount <= 0:
                continue
            if leg_key.startswith("carrier:"):
                leg_strategy = _encode_strategy(dca_strategy, trend, trend_factor, buy_time=buy_time) + f"/carrier/{leg_key[8:]}"
            elif leg_key == "index":
                leg_strategy = _encode_strategy(dca_strategy, trend, trend_factor, buy_time=buy_time)
            elif leg_key in ("588200", "512480"):
                leg_strategy = _encode_strategy("split10", trend, trend_factor, buy_time=buy_time) + f"/{leg_key}"
            else:
                leg_strategy = _encode_strategy(dca_strategy, trend, trend_factor, buy_time=buy_time) + f"/sector/{leg_key}"
            ok, order_info = _place_buy_order(leg_etf, leg_amount, reason)
            if ok:
                _record_dca_log(
                    fund_code=fund_code,
                    window_start=window_start,
                    buy_day=current_day,
                    etf_code=leg_etf,
                    amount=leg_amount,
                    strategy=leg_strategy,
                    order_id=order_info,
                    status="filled",
                    schedule_day=schedule_day, trend_factor=trend_factor,
                )
            else:
                _record_dca_log(
                    fund_code=fund_code,
                    window_start=window_start,
                    buy_day=current_day,
                    etf_code=leg_etf,
                    amount=leg_amount,
                    strategy=leg_strategy,
                    order_id="",
                    status="failed",
                    schedule_day=schedule_day, trend_factor=trend_factor,
                )
                results.append(
                    f"❌ {idx['index_name']} {leg_etf}: 买入未成交 ({order_info})"
                )

        executed_count += 1
        total_invested_today += daily_amount
        carrier = _carrier_active(fund_code)
        if carrier:
            target_desc = "载体ETF(" + "+".join(f"{leg_etf} {leg_amount:.0f}" for _, leg_etf, leg_amount in legs) + ")"
        elif CHINA_INDICES.get(fund_code, {}).get("guide_only") and _sector.get_sector_config().get("enabled"):
            target_desc = "板块ETF(" + "+".join(f"{leg_etf} {leg_amount:.0f}" for _, leg_etf, leg_amount in legs) + ")"
        else:
            target_desc = etf_code
        results.append(
            f"📢 {idx['index_name']} {target_desc}: "
            f"¥{daily_amount:.0f} [{dca_strategy}/{position_tier}] (第{schedule_day}天)"
            + (f" | {'; '.join(fallback_notes)}" if fallback_notes else "")
        )

    # ── 全行业轨（enabled+execute 真实下单 / 默认 dry-run 计划；闸门关闭仅监测）──
    ind_track = _run_industry_track(as_of or today_str, today_str, gate_open=(liquidity_gate != "closed"))
    if ind_track["lines"]:
        results.extend(ind_track["lines"])

    # 4. 构建结构化结果
    skipped_drop = sum(1 for i in indices if i.get("tier") in ("drop", "watch") and i["status"] != "normal")
    turning_count = window.get("turning_count", 0)
    pre_turn_count = len(tradeable) - turning_count
    pit_count = window.get("pit_count", 0)
    phase = window.get("phase", "idle")
    phase_label = {"buying": "买入窗口", "waiting": "等待拐点"}.get(phase, phase)
    resonance = _resonance_multiplier(indices)
    gate_info = f"🔒闸门关闭" if liquidity_gate == "closed" else f"🔓闸门开启"

    # 传统文本摘要（Pi 不可用时的 fallback）
    summary_lines = [
        f"黄金坑 DCA v5 (DCA基准·趋势调节 · 仅通知) — {today_str}",
        f"阶段: {phase_label} | 领先: {window.get('leading_index', 'N/A')}",
        f"可交易: {len(tradeable)} | 拐点已确认: {turning_count} | 拐点前: {pre_turn_count}",
        f"共振: {pit_count}指数入坑 → {resonance:.1f}x | 宏观: {macro_coef:.1f}x | {gate_info}",
        f"通知: {executed_count}笔 ¥{total_invested_today:.0f} | 跳过: {skipped_count}笔",
        f"放弃/watch: {skipped_drop}个指数不入金",
        "",
    ]
    if results:
        summary_lines.extend(results)
    else:
        summary_lines.append("本日无符合条件的定投通知")
    summary_lines.append("")

    if pre_turn_count > 0 and turning_count == 0:
        summary_lines.append("💡 趋势未确认: DCA基准×0.1~0.5x减速建仓, 等待贪婪值连续回升。")
    elif turning_count > 0 and pre_turn_count > 0:
        summary_lines.append(f"💡 部分拐点确认: {turning_count}个指数已回升(DCA×1.0~1.5x加速), {pre_turn_count}个仍在等待。")
    elif turning_count > 0:
        summary_lines.append(f"💡 拐点已确认: {turning_count}个指数 DCA×1.0~1.5x加速加仓中。")

    if macro_coef < 1.0:
        summary_lines.append(f"🌍 全球宏观系数 {macro_coef:.1f}x: 仓位已下调 ({global_macro.get('summary', '')})")
    cf = global_macro.get("capital_flow", {})
    if cf.get("summary"):
        summary_lines.append(f"💰 资金流向: {cf['summary']}")
    divergent_names = [i["index_name"] for i in indices if i.get("turning_validation") == "divergent"]
    if divergent_names:
        summary_lines.append(f"⚠️ 全球趋势背离: {', '.join(divergent_names)} 仓位已限制在 declining 因子水平")

    # ── 深度入坑告警: 任一指数连续入坑 ≥30 天 ──
    deep_pit_warnings = []
    for idx in indices:
        days_in = idx.get("days_in_pit") or idx.get("days_in_warning") or 0
        if days_in >= 30:
            deep_pit_warnings.append(
                f"⚠️ 深度入坑告警: {idx['index_name']} 已连续入坑 {days_in} 天 "
                f"(贪婪={idx.get('greed', 0):.4f}), 建议人工复核参数"
            )
    if deep_pit_warnings:
        summary_lines.append("")
        summary_lines.extend(deep_pit_warnings)

    # ── 板块拆分选筹摘要（guide_only 宽基活跃时展示）──
    if _sector.get_sector_config().get("enabled") and any(
        CHINA_INDICES.get(i["fund_code"], {}).get("guide_only") for i in tradeable
    ):
        sel_mode, _ = _sector.resolve_regime_mode()
        sel = _sector.select_sectors(as_of=as_of, mode=sel_mode)
        summary_lines.append(_sector.format_selection(sel))
        summary_lines.append("")

    summary_text = "\n".join(summary_lines)

    # 构建 buy_candidates 结构化列表
    buy_candidates = []
    for i, idx in enumerate(tradeable):
        fund_code = idx["fund_code"]
        cfg = config_by_fund.get(fund_code)
        if not cfg:
            continue
        max_total = cfg["max_total_amount"]
        index_params = get_effective_index_config(fund_code)
        pos_mult = index_params.get("position_multiplier", 1.0)
        dca_strategy = index_params.get("dca_strategy", "uniform_10")
        entry_greed = index_params.get("entry_greed", 0.50)
        current_greed = idx.get("greed", 0.0)

        total_invested = sum(
            _get_day_amount(fund_code, window_start, d)
            for d in _get_executed_days(fund_code, window_start)
        )
        remaining = max_total - total_invested
        turning_confirmed = idx.get("turning_point_confirmed", False)
        days_rising = idx.get("days_rising", 0)
        trend = idx.get("trend", "declining")

        schedule_day = current_day
        dca_weights = _strategy_weights(dca_strategy)
        dca_weight = dca_weights[min(schedule_day, PIT_WINDOW_DAYS - 1)]
        trend_factor = get_trend_factor(
            trend=trend, days_rising=days_rising,
            fund_code=fund_code,
            current_greed=current_greed, entry_greed=entry_greed,
        )

        daily_amount = max_total * dca_weight * trend_factor * pos_mult * resonance * macro_coef
        daily_amount = min(daily_amount, remaining)
        position_tier = _trend_state_label(days_rising)

        buy_candidates.append({
            "index_name": idx["index_name"],
            "fund_code": fund_code,
            "etf_code": cfg["etf_code"],
            "tier": idx.get("tier", ""),
            "status": idx["status"],
            "position_tier": position_tier,
            "dca_strategy": dca_strategy,
            "dca_weight": round(dca_weight, 3),
            "trend_factor": round(trend_factor, 2),
            "schedule_day": schedule_day,
            "daily_amount": round(daily_amount, 0),
            "max_total": max_total,
            "total_invested": round(total_invested, 0),
            "remaining": round(remaining, 0),
            "trend": trend,
            "days_rising": days_rising,
            "days_in_pit": idx.get("days_in_pit") or idx.get("days_in_warning") or 0,
            "greed": idx.get("greed", 0),
            "percentile": idx.get("percentile", 0),
            "turning_confirmed": turning_confirmed,
            "reason": (
                f"dca={dca_strategy}(w{dca_weight:.2f}) trend={trend}(f{trend_factor:.1f}) "
                f"resonance={resonance:.1f}x macro={macro_coef:.1f}x day{schedule_day}"
            ),
        })

    # 构建 skipped 列表
    skipped_list = []
    for idx in indices:
        if idx.get("tier") in ("drop", "watch") and idx["status"] != "normal":
            fund_code = idx["fund_code"]
            cfg = config_by_fund.get(fund_code)
            skipped_list.append({
                "index_name": idx["index_name"],
                "fund_code": fund_code,
                "etf_code": cfg["etf_code"] if cfg else "",
                "tier": idx.get("tier", ""),
                "status": idx["status"],
                "reason": f"tier={idx.get('tier')}, 不入金",
            })

    return {
        "as_of": as_of,
        "today": today_str,
        "phase": phase,
        "phase_label": phase_label,
        "window_day": current_day,
        "window_start": window_start,
        "leading_index": window.get("leading_index", ""),
        "resonance": {
            "pit_count": pit_count,
            "multiplier": resonance,
        },
        "macro": {
            "coefficient": macro_coef,
            "liquidity_gate": liquidity_gate,
            "summary": global_macro.get("summary", ""),
            "capital_flow_summary": cf.get("summary", "") if cf else "",
        },
        "holdings": _get_holdings_detail(),
        "exit_signals": [
            {
                "index_name": i["index_name"],
                "etf_code": config_by_fund.get(i["fund_code"], {}).get("etf_code", ""),
                "fund_code": i["fund_code"],
                "signal": i.get("exit_signal"),
                "reason": i.get("exit_reason", ""),
                "greed": i.get("greed", 0),
                "percentile": i.get("percentile", 0),
            }
            for i in indices
            if i.get("exit_signal")
            and config_by_fund.get(i["fund_code"])
            and _get_holding_shares(config_by_fund[i["fund_code"]]["etf_code"]) >= 100
        ],
        "buy_candidates": buy_candidates,
        "skipped": skipped_list,
        "stats": {
            "tradeable_count": len(tradeable),
            "turning_count": turning_count,
            "pre_turn_count": pre_turn_count,
            "executed_count": executed_count,
            "total_invested_today": round(total_invested_today, 0),
            "skipped_count": skipped_count,
            "skipped_drop": skipped_drop,
            "divergent": divergent_names,
        },
        "tips": summary_lines[-6:] if len(summary_lines) > 6 else summary_lines[3:],
        "summary_text": summary_text,
        "industry_monitor": ind_track["monitor"],
    }


def _run_industry_track(as_of: str, today_str: str, gate_open: bool = True) -> Dict[str, Any]:
    """全行业轨：dry-run 计划 / 真实下单（enabled+execute+闸门开启）+ 监测视图。

    返回 {"monitor": Dict, "lines": List[str], "active": bool, "dry_run": bool}。
    行业轨异常不影响指数级 DCA（仅记录日志并回退空结构）。
    当日幂等：advance_industry_windows 同一天已推进则重放今日记录，不会重复下单。
    """
    try:
        from app.services.golden_pit_industry_service import (
            INDUSTRY_BY_ID, _account_summary, advance_industry_windows,
            get_industry_config, industry_signal, load_industry_greed,
            load_industry_px, save_industry_state,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("行业轨模块加载失败: %s", e)
        return {"monitor": _build_industry_monitor(as_of), "lines": [], "active": False, "dry_run": True}

    try:
        cfg = get_industry_config()
        if not cfg.get("enabled"):
            return {"monitor": _build_industry_monitor(as_of), "lines": [], "active": False, "dry_run": True}

        pool: List[Dict[str, Any]] = cfg.get("pool", [])
        execute = bool(cfg.get("execute")) and gate_open
        greed = load_industry_greed()
        px = load_industry_px()
        acct = _account_summary()
        nav = acct.get("cash", acct.get("initial_capital", 250000.0))

        signals: Dict[str, Dict[str, Any]] = {}
        for ind in pool:
            iid = ind["id"]
            try:
                signals[iid] = industry_signal(
                    ind, greed.get(ind.get("greed_code", ""), {}),
                    px.get(ind.get("etf_code", ""), {}), as_of,
                    float(cfg["pit_pct"]), float(cfg["drawdown_pct"]), float(cfg["entry_cap"]),
                )
            except Exception:  # noqa: BLE001 - 单行业信号失败不阻断
                signals[iid] = {"greed": None, "greed_pct": None, "drawdown": 0.0, "in_pit": False, "overheat": False}

        adv = advance_industry_windows(as_of, signals, px, pool, cfg, nav, execute=execute)
        lines: List[str] = []
        replayed = bool(adv.get("replayed"))

        if execute and not replayed:
            # ── 买入: 资金池裁决后的实际金额（按 priority 分配）──
            win_by_id = {k: v for k, v in adv["windows"].items()}
            for alloc in adv.get("allocations", []):
                iid = alloc["id"]
                actual = float(alloc.get("actual", 0.0))
                if actual < 1e-6:
                    continue
                ind = INDUSTRY_BY_ID.get(iid) or next((x for x in pool if x.get("id") == iid), None)
                if not ind:
                    continue
                etf = _normalize_carrier_etf_code(str(ind.get("etf_code", "")))
                w = win_by_id.get(iid) or {}
                reason = f"[黄金坑行业DCA] {ind.get('name', iid)} priority={ind.get('priority', 99)}"
                ok, order_info = _place_buy_order(etf, actual, reason)
                _record_dca_log(
                    fund_code=f"industry/{iid}",
                    window_start=w.get("win_start") or today_str,
                    buy_day=max(0, int(w.get("win_day", 1)) - 1),
                    etf_code=etf,
                    amount=round(actual, 2),
                    strategy=f"industry/{iid}",
                    order_id=order_info if ok else "",
                    status="filled" if ok else "failed",
                    schedule_day=0, trend_factor=0.0,
                )
                if ok:
                    lines.append(f"🏭 {ind.get('name', iid)} {etf}: 行业定投 ¥{actual:.0f} (order: {order_info})")
                else:
                    # 买入失败: 回滚该窗口 invested 并滚入 leftover，避免资金虚计
                    if w:
                        w["invested"] = max(0.0, float(w.get("invested", 0.0)) - actual)
                        w["leftover"] = float(w.get("leftover", 0.0)) + actual
                    lines.append(f"❌ {ind.get('name', iid)} {etf}: 买入未成交 ({order_info})，额度滚动次日")

            # ── 卖出: 出场窗口全仓 ──
            for ex in adv.get("exits", []):
                iid = ex["id"]
                etf = _normalize_carrier_etf_code(str(ex.get("etf_code", "")))
                qty = float(ex.get("qty", 0.0) or 0.0)
                shares = int(qty / 100) * 100
                ind_name = ex.get("name", iid)
                reason = f"[黄金坑行业DCA出场] {ind_name} {ex.get('reason', '')}"
                if shares < 100:
                    lines.append(f"⚠️ {ind_name} 出场[{ex.get('reason')}] 份额不足100股({qty:.0f}股), 跳过卖单")
                    continue
                ok, order_info = _place_sell_order(etf, shares, reason)
                _record_dca_log(
                    fund_code=f"industry/{iid}",
                    window_start=ex.get("start") or today_str,
                    buy_day=max(0, int(ex.get("win_day", 0))),
                    etf_code=etf,
                    amount=round(float(ex.get("invested", 0.0)), 2),
                    strategy=f"exit/industry/{ex.get('reason', 'exit')}",
                    order_id=order_info if ok else "",
                    status="filled" if ok else "failed",
                    schedule_day=0, trend_factor=0.0,
                )
                if ok:
                    lines.append(f"🏁 {ind_name} {etf}: 出场[{ex.get('reason')}] 收益{float(ex.get('ret', 0)) * 100:+.2f}% 卖出{shares}股 (order: {order_info})")
                else:
                    lines.append(f"❌ {ind_name} 出场卖出未成交 ({order_info})")

            save_industry_state(adv["state"])
        elif not execute:
            lines.extend(adv.get("notes", [])[-5:])
            if adv.get("cut_items"):
                lines.append(f"⏳ 资金池裁剪: {len(adv['cut_items'])}项 现金不足滚动次日")

        # ── 监测视图（与快照同构）──
        win_by_id = {k: v for k, v in adv["windows"].items()}
        plan_by_id = {p["id"]: p for p in adv.get("plans", [])}
        actual_by_id = {a["id"]: a["actual"] for a in adv.get("allocations", [])} if "allocations" in adv else {}
        industries_view: List[Dict[str, Any]] = []
        for ind in pool:
            iid = ind["id"]
            sig = signals.get(iid, {})
            w = win_by_id.get(iid)
            p = plan_by_id.get(iid)
            industries_view.append({
                "id": iid, "name": ind.get("name"), "greed_code": ind.get("greed_code"),
                "etf_code": ind.get("etf_code"), "priority": ind.get("priority", 99),
                "close": px.get(ind.get("etf_code", ""), {}).get(as_of),
                "greed": sig.get("greed"), "greed_pct": sig.get("greed_pct"),
                "drawdown": round(float(sig.get("drawdown", 0.0)), 4),
                "in_pit": bool(sig.get("in_pit")), "overheat": bool(sig.get("overheat")),
                "window_day": (w or {}).get("win_day", 0),
                "planned_amount": round(float((p or {}).get("amount", 0.0)), 2),
                "actual_amount": round(float(actual_by_id.get(iid, 0.0)), 2),
                "total_invested": round(float((w or {}).get("invested", 0.0)), 2),
            })
        cash_floor = nav * float(cfg["cash_min_pct"])
        cash_pool = {
            "total_nav": round(nav, 2),
            "cash": round(acct.get("cash", nav), 2),
            "cash_min_pct": float(cfg["cash_min_pct"]),
            "cash_floor": round(cash_floor, 2),
            "available_cash": round(max(0.0, nav - cash_floor), 2),
            "planned_total": round(float(adv.get("planned_total", 0.0)), 2),
            "actual_total": round(float(adv.get("actual_total", 0.0)), 2),
            "cut_items": adv.get("cut_items", [])[:10],
            "enabled": True,
            "execute": execute,
            "dry_run": not execute,
        }
        monitor = {"as_of": as_of, "enabled": True, "industries": industries_view,
                   "cash_pool": cash_pool, "notes": adv.get("notes", [])}
        return {"monitor": monitor, "lines": lines, "active": True, "dry_run": not execute}
    except Exception as e:  # noqa: BLE001 - 行业轨失败不影响指数级 DCA
        logger.warning("行业轨执行失败: %s", e)
        return {"monitor": _build_industry_monitor(as_of), "lines": [], "active": False, "dry_run": True}


def _build_industry_monitor(as_of: str) -> Dict[str, Any]:
    """行业轨（dry-run 计划 + 资金池视图）；industry_pool_enabled=true 时生成，默认关闭返回空结构。"""
    try:
        from app.services.golden_pit_industry_service import get_industry_config, industry_monitor_snapshot
        cfg = get_industry_config()
        if not cfg.get("enabled"):
            return {"as_of": as_of, "enabled": False, "industries": [], "cash_pool": {}, "notes": [],
                    "dry_run": True, "reason": "industry_pool_enabled=false"}
        return industry_monitor_snapshot(as_of)
    except Exception as e:  # noqa: BLE001 - 行业轨失败不影响指数级 DCA
        logger.warning("行业轨 dry-run 计划生成失败: %s", e)
        return {"as_of": as_of, "enabled": False, "industries": [], "cash_pool": {}, "notes": [],
                "dry_run": True, "error": str(e)}


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
                    GoldenPitDCALog.status.in_(("filled", "notified")),
                    GoldenPitDCALog.strategy.notlike("exit/%"),
                )
                .all()
            )
            return sum(r.amount for r in rows)
        finally:
            db.close()
    except Exception:
        return 0.0
