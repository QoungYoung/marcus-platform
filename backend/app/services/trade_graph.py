# -*- coding: utf-8 -*-
"""
LangGraph 交易决策流程编排

将原本完全由 Prompt SOP 驱动的 7 步交易决策流程，
拆分为确定性节点（数据获取、安全门检查）和 LLM 决策节点（选股分析、下单执行）。

图结构:
  fetch_context → check_safety_gates → [条件分岔]
    ├─ hard_blocked → handle_blocked → END
    └─ cleared → call_pi_decision → process_result → END

与旧版的关键区别:
  - 基础数据（扫描报告、持仓）由前置节点直接获取，不再依赖 LLM 调用工具
  - 安全门检查（回撤、连续亏损熔断）在代码层判定，不经过 LLM
  - Pi 只负责分析判断和下单执行，工具集可以更聚焦
"""

import os
import json
import logging
import re
import ssl
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import TypedDict, Optional, Dict, Any

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)

# 安全门临时旁路开关 —— 设为 True 跳过回撤/熔断检查
SAFETY_GATE_BYPASS = True


# ═══════════════════════════════════════════════════════════
# State
# ═══════════════════════════════════════════════════════════

class TradeState(TypedDict):
    task_id: str
    execution_id: str
    window: str          # morning | mid_morning | late_morning | afternoon | closing

    # 预获取数据
    scan_report_text: str
    portfolio_json: str
    pool_context: str
    stance_context: str
    trade_mode_instruction: str
    regime_context: str           # 市场结构指令（趋势/震荡）
    market_regime: str            # "trend" / "oscillation"

    # 安全门
    drawdown_pct: float
    consecutive_losses: int
    hard_blocked: bool
    block_reason: str

    # 策略合规
    regime_violation: bool
    regime_violation_reason: str

    # Pi 决策结果
    pi_raw_reply: str
    pi_stance: str
    pi_position_limit: int
    pi_reason: str

    # 输出
    report: str
    error: str


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _get_workspace() -> Path:
    try:
        from app.config import get_settings
        if hasattr(get_settings(), 'workspace_path'):
            return get_settings().workspace_path
    except Exception:
        pass
    return Path(__file__).parent.parent.parent.parent.parent


def _get_pi_server_url() -> str:
    try:
        from app.config import get_settings
        return get_settings().PI_SERVER_URL
    except Exception:
        return 'http://localhost:3001/chat'


def _infer_window(task_id: str, pi_prompt: str) -> str:
    """根据 task_id 和 pi_prompt 推断时间窗口"""
    ctx = f"{task_id} {pi_prompt}".lower()
    if 'plan' in ctx or 'trigger' in ctx:
        return 'plan_trigger'
    if 'closing' in ctx:
        return 'closing'
    if 'mid_morning' in ctx:
        return 'mid_morning'
    if 'late_morning' in ctx or 'late' in ctx:
        return 'late_morning'
    if 'afternoon' in ctx:
        return 'afternoon'
    if 'morning' in ctx or 'early' in ctx:
        return 'morning'
    return 'morning'


def _get_market_values(positions: list) -> dict:
    """通过雪球引擎获取持仓实时市价，计算总市值。
    返回 {"market_value": float, "prices": dict}。
    失败时回退到成本价。
    """
    if not positions:
        return {"market_value": 0.0, "prices": {}}
    try:
        import sys as _sys
        _core_dir = str(_get_workspace() / "core")
        if _core_dir not in _sys.path:
            _sys.path.insert(0, _core_dir)
        from xueqiu_engine import XueqiuEngine
        xq_config = str(_get_workspace() / "core" / "config.json")
        xq = XueqiuEngine(config_file=xq_config)

        market_value = 0.0
        prices = {}
        for pos in positions:
            sym = pos['symbol']
            try:
                quote = xq.get_stock_quote(sym, use_cache=True)
                if quote:
                    price = quote.get('current', pos['avg_cost'])
                else:
                    price = pos['avg_cost']
            except Exception:
                price = pos['avg_cost']
            market_value += price * pos['volume']
            prices[sym] = price
        return {"market_value": round(market_value, 2), "prices": prices}
    except Exception:
        cost = sum(p['avg_cost'] * p['volume'] for p in positions)
        return {"market_value": cost, "prices": {}}


def _read_scan_report() -> str:
    """读取最新扫描报告（读取文件而非 HTTP 调用，避免进程内 HTTP 开销）"""
    workspace = _get_workspace()
    scan_dir = workspace / "memory" / "market-scan-logs"
    if not scan_dir.exists():
        return "（无扫描报告）"

    today = datetime.now().strftime('%Y-%m-%d')
    scan_file = scan_dir / f"{today}-scans.jsonl"
    if not scan_file.exists():
        files = sorted(scan_dir.glob("*-scans.jsonl"), reverse=True)
        scan_file = files[0] if files else None
    if not scan_file:
        return "（无扫描报告）"

    try:
        lines = []
        with open(scan_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
        if lines:
            return json.dumps(json.loads(lines[-1]), ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[TradeGraph] 扫描报告读取失败: {e}")
    return "（扫描报告读取失败）"


def _read_portfolio() -> str:
    """读取账户持仓数据（从 PostgreSQL paper_* 表计算）"""
    try:
        from app.database import SessionLocal
        from app.models.paper_trade import PaperTrade, PaperPosition, PaperAccountInfo
        from sqlalchemy import func

        db = SessionLocal()
        try:
            acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.account_id == 'stock').first()
            initial_cap = float(acct.initial_capital) if acct else 100000.0
            # 现金从 paper_account_info 直接读取（paper engine 维护，含佣金/费用调整后的准确值）
            available_cash = float(acct.available_cash) if acct else (initial_cap - 0)

            # 当前持仓标的元数据（stock 账户）
            pos_rows = db.query(PaperPosition).filter(PaperPosition.account_id == 'stock').all()
            held_symbols = {r.symbol: r for r in pos_rows}

            # 全部非撤回成交（stock 账户）
            trades = db.query(PaperTrade).filter(
                PaperTrade.account_id == 'stock',
                PaperTrade.volume > 0,
                (PaperTrade.voided == 0) | (PaperTrade.voided == None)
            ).order_by(PaperTrade.created_at).all()

            total_profit = float(
                db.query(func.coalesce(func.sum(PaperTrade.profit), 0)).filter(
                    PaperTrade.account_id == 'stock',
                    (PaperTrade.voided == 0) | (PaperTrade.voided == None)
                ).scalar() or 0
            )
        finally:
            db.close()

        # 按标的汇总持仓量和成本
        symbol_pos: dict = {}
        for t in trades:
            sym = t.symbol
            if sym not in symbol_pos:
                symbol_pos[sym] = {'buy_volume': 0, 'buy_amount': 0.0, 'sell_volume': 0, 'sell_amount': 0.0}
            if t.direction == '买入':
                symbol_pos[sym]['buy_volume'] += (t.volume or 0)
                symbol_pos[sym]['buy_amount'] += (t.amount or 0)
            elif t.direction == '卖出':
                symbol_pos[sym]['sell_volume'] += (t.volume or 0)
                symbol_pos[sym]['sell_amount'] += (t.amount or 0)

        positions = []
        for sym, pos in symbol_pos.items():
            net_vol = pos['buy_volume'] - pos['sell_volume']
            if net_vol > 0:
                avg_cost = pos['buy_amount'] / pos['buy_volume'] if pos['buy_volume'] > 0 else 0
                entry_info = held_symbols.get(sym)
                positions.append({
                    "symbol": sym,
                    "volume": net_vol,
                    "avg_cost": round(avg_cost, 2),
                    "entry_date": entry_info.entry_date if entry_info else '',
                    "highest_price": entry_info.highest_price if entry_info else None,
                })

        total_buy = sum(t.amount or 0 for t in trades if t.direction == '买入')
        total_sell = sum(t.amount or 0 for t in trades if t.direction == '卖出')
        cash = available_cash
        total_cost = sum(p['avg_cost'] * p['volume'] for p in positions)

        mv_result = _get_market_values(positions)
        market_value = mv_result["market_value"]
        total_asset_market = cash + market_value
        total_asset = cash + total_cost

        from app.core.peak_equity import save_peak_equity, load_peak_equity
        save_peak_equity(total_asset_market)
        peak_equity = load_peak_equity(fallback=max(100000, total_asset_market))

        today = datetime.now().strftime('%Y-%m-%d')
        today_bought = list(set(
            t.symbol for t in trades
            if t.direction == '买入' and (t.trade_date or '') == today
        ))

        return json.dumps({
            "initial_capital": initial_cap,
            "cash": round(cash, 2),
            "total_cost": round(total_cost, 2),
            "total_asset": round(total_asset, 2),
            "total_asset_market": round(total_asset_market, 2),
            "market_value": round(market_value, 2),
            "peak_equity": round(peak_equity, 2),
            "total_profit": round(total_profit, 2),
            "position_count": len(positions),
            "today_bought": today_bought,
            "positions": positions,
        }, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"[TradeGraph] 持仓读取失败: {e}")
        return "{}"


def _read_pool_context(task_id: str) -> str:
    """获取候选池上下文"""
    if 'closing' in task_id.lower():
        return ""
    try:
        from app.services.candidate_pool import get_candidate_pool
        pool = get_candidate_pool()
        pool.expire_stale()
        pool.cleanup_sold_promoted()
        active = pool.get_all_active()
        promoted = pool.get_promoted()
        if active or promoted:
            return pool.format_for_pi()
    except Exception as e:
        logger.warning(f"[TradeGraph] 候选池读取失败: {e}")
    return ""


def _read_stance_context() -> str:
    """获取上一轮 Pi 立场"""
    try:
        from core.utils.strategy_chain import StrategyChain
        chain = StrategyChain()
        pi_conf = chain.get_pi_confirmation()
        if pi_conf:
            return (
                f"\n📌 你上一轮的判断：{pi_conf.get('stance', 'yellow')}"
                f" / 仓位上限 {pi_conf.get('position_limit', 60)}%"
                f" — 理由：{pi_conf.get('reason', '无')}\n"
            )
    except Exception:
        pass
    return ""


def _read_market_regime() -> tuple:
    """从 market_diagnosis 表（PostgreSQL）读取今日市场结构。

    Returns:
        (regime: str, label: str, suggestion: str)
        regime: "trend" / "oscillation" / "unknown"
    """
    try:
        from app.database import SessionLocal
        from app.models.market_orm import MarketDiagnosis
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y%m%d")
            row = db.query(MarketDiagnosis).filter(
                MarketDiagnosis.trade_date == today
            ).first()
            if row:
                return (row.state, row.label, row.suggestion)
        finally:
            db.close()
    except Exception:
        pass
    return ("unknown", "未知", "⚠️ 今日尚未执行盘前诊断（9:10），无法确定市场结构。禁止按趋势市默认策略操作！")


def _read_style_regime() -> dict:
    """从 market_diagnosis 表读取今日风格轮动信号。

    Returns:
        dict with keys: style_regime, consecutive_days, suggestion
    """
    try:
        import json as _json
        from app.database import SessionLocal
        from app.models.market_orm import MarketDiagnosis
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y%m%d")
            row = db.query(MarketDiagnosis).filter(
                MarketDiagnosis.trade_date == today
            ).first()
            if row and row.indicators_json:
                indicators = _json.loads(row.indicators_json)
                sr = indicators.get("style_rotation", {})
                return {
                    "style_regime": sr.get("style_regime", "NEUTRAL"),
                    "consecutive_days": sr.get("consecutive_days", 0),
                    "suggestion": sr.get("suggestion", ""),
                    "divergence_warning": sr.get("divergence_warning"),
                }
        finally:
            db.close()
    except Exception:
        pass
    return {"style_regime": "NEUTRAL", "consecutive_days": 0,
            "suggestion": "", "divergence_warning": None}


def _get_style_strategy(style_info: dict) -> str:
    """风格/主线偏好提示(狼大口径, 2026-09-03 审计: 去掉平台科技20%/防御10%/资源20%固定仓位上限).
    狼大按"主线/题材"偏好选股(主线随年代切换、逐期重推), 不设风格仓位硬限; 仓位按 calc_position 三档与狼大买点决定."""
    regime = style_info.get("style_regime", "NEUTRAL")
    days = style_info.get("consecutive_days", 0)
    suggestion = style_info.get("suggestion", "")
    warning = style_info.get("divergence_warning")
    pref = {"OFFENSE": "科技成长(半导体/AI/算力/机器人)", "DEFENSE": "防御/低位(银行/红利/公用事业)",
            "RESOURCE_HEDGE": "资源/黄金/有色/能源"}.get(regime, "均衡")
    block = ("\n📊 **风格/主线偏好(狼大口径)**：当前《" + str(pref) + "》为市场偏好方向（连续 " + str(days) + " 天）。\n"
             "选股按主线/题材偏好优先（主线逐期重推，不固化）；不设平台风格仓位上限，\n"
             "仓位按 calc_position 三档(试探/确认/冲刺)与狼大买点(日内回撤≥2.5%/触前低+缩量+站回黄线 或 计划/关键位命中)决定。\n")
    if warning:
        block += "\n" + warning + "\n"
    if suggestion:
        block += "\n风格建议: " + suggestion + "\n"
    return block
def _get_regime_strategy(regime: str) -> str:
    """根据市场结构生成策略切换指令块"""
    if regime == "oscillation":
        return (
            "\n📊 **今日市场结构：🟡 震荡（日度指标，非月度regime）**\n"
            "⚠️ 月度门控：短期层（动量/做T/短线）仅「趋势向上月」允许开新仓；**震荡日/震荡月短期层只做T不新开**。若 external.us_risk=true → 降科技仓位/暂停科技新开。\n"
            "你必须严格遵循以下月度门控参数，不得使用趋势月策略：\n\n"
            "| 参数 | 🟡 震荡（当前） |\n"
            "|------|:------:|\n"
            "| 新开仓 | **禁止**（短期层只做T不新开；仅趋势向上月允许） |\n"
            "| 操作方式 | **只做T摊薄**：正T低吸(指数回撤2-3%)/分时T出/黄线离场；T仓与底仓分离，底仓不卖 |\n"
            "| 做T周期 | 分时做T（正T低吸/黄线/缩量转放量），不要求60分钟金叉 |\n"
            "| 做T仓 | ≤5%（底仓另计）；主线低吸等建仓不设平台单票硬限 |\n"
            "| 产业链/主线建仓 | 允许（若满足狼大买点：日内回撤≥2.5%/触前低 + 缩量 + 站回黄线，或 计划/关键位命中）；不追主升 |\n\n"
            "⚠️ 震荡日节奏（关键！）：\n"
            "- 短期层禁止新开仓、禁止加仓；持仓只做T摊薄（做T由 t_monitor 30s 实时监控）\n"
            "- 做T标的（有 t_conditions）卖出最多卖 T 仓（持仓-100），底仓不卖（狼大铁律，代码层强制拦截）\n"
            "- 🚫 分钟数据硬门槛保留：分钟数据不可用 = 不建仓（没有例外）\n"
        )
    elif regime == "trend":
        return (
            "\n📊 **今日市场结构：🟢 主升浪/趋势（狼大口径）**\n"
            "· 大级别：主升浪才做主线；按 完整产业链形态 + 新逻辑 + 新资金进场 + 抗跌主升确定 判定主线，前排龙头/趋势票优先。\n"
            "· 买点：主线低吸 = 日内回撤≥2.5% 或 触前低 + 缩量 + 站回黄线；或 计划/关键位命中。\n"
            "   不追突破、不追高；高位(接近前高+量价背离/双头) → 只做T/逢高减仓/防顶。\n"
            "· 仓位：按 calc_position 三档(试探/确认/冲刺)；不设平台固定单票/总仓上限。\n"
            "· 持有/止盈：趋势不走不止盈；确认制T出(停量+二次不过前高) 或 趋势走弱再减。\n"
            "· 底仓不卖、T仓做T(由TMonitor 30s执行)；每条建仓/加仓前必须过 check_entry_filters + calc_position。\n"
            "· 不设平台均线/盈亏比/追入硬参数；也不等固定时钟。\n"
        )
    else:
        return (
            "\n⚠️ **今日市场结构：未诊断**\n"
            "今日尚未执行盘前诊断（9:10 由 morning_diagnosis 任务执行），无法确定市场结构。\n\n"
            "⛔ **严格禁止按任何默认策略操作！**\n"
            "- ❌ 禁止假设为趋势市\n"
            "- ❌ 禁止假设为震荡市\n"
            "- ✅ 只能执行以下安全操作：\n"
            "  1. 检查持仓盈亏状态\n"
            "  2. 执行止损/止盈（如果触发阈值）\n"
            "  3. 报告当前持仓状态\n"
            "- ⛔ 禁止建新仓！禁止加仓！\n\n"
            "等待盘前诊断完成后，在下一次交易窗口再执行建仓操作。\n"
        )


def _get_trade_instruction(window: str, regime: str = "unknown") -> str:
    """狼大口径交易模式指令(2026-09-03 审计: 不再注入震荡/趋势市状态, 大级别一律以 wave_context(浪型)为准;
    本函数不再按市场结构(震荡/趋势)分流, 避免与狼大浪型/主线逻辑打架)."""
    if window == 'plan_trigger':
        return (
            "**本次为【计划触发】执行**（非等待窗口）：\n"
            "严格按 plan_context 中「已命中/待触发」的计划动作立即执行（回补 T 仓 / 低位建仓 / 减半锁定）。\n"
            "不受常规时间窗口节奏限制；但 P2 Gate(浪型/宏观)、仓位三档、狼大纪律(周末降仓/只做T不追主升) 等硬门控仍生效。\n"
        )
    _base = (
        "【狼大口径·按浪型/主线执行，不套用震荡/趋势市状态】\n"
        "· 大级别以 wave_context(浪型) 为准：主升浪→主线低吸/持有（前排龙头优先，不追突破/不追高）；\n"
        "   调整浪/震荡(B反/4-5/C杀)→只做T、不追主升。\n"
        "· 主线低吸/新开须满足狼大买点：日内回撤≥2.5% 或 触前低 + 缩量 + 站回黄线；或 计划/关键位命中（六因子软指导已注入）。\n"
        "· 不追高：接近前高/高位 → 只做T/逢高减仓/防顶。\n"
        "· 做T：正T低吸(-2.5%/触前低)→确认制T出(放量→高点→停量→二次不过前高)/黄线跌破离场；只卖T仓，底仓不卖；\n"
        "   做T由 TMonitor 30s 实时执行，你在报告中确认即可。\n"
        "· 仓位按 calc_position 三档(试探/确认/冲刺)，不设平台固定单票/总仓上限。\n"
        "· 每条建仓/加仓前必须过 check_entry_filters + calc_position。\n"
        "· 若工具返回“震荡市/60分钟右侧/持仓1-3天”等市场状态建议：属早期右侧策略废弃文案，一律忽略，市场判断以 wave_context(浪型)与 main_line_context(主线)为准，报告不要引用。\n"
        "SIGNAL: <按浪型> POSITION:<当前仓位> REASON:按狼大浪型/主线执行"
    )
    if window == 'morning':
        # ⑧ 开盘主线候选建仓/试仓：由 agent 按指令+上下文判断（不再用独立 9:31 规则脚本通道）
        return (
            "【开盘(9:35)主线候选建仓/试仓——由你按指令+上下文判断】\n"
            "· 开盘时段优先评估主线候选：main_line_state.candidates 主题 → THEME_CONCEPTS(主题概念) → get_component_stocks 枚举成分股，逐一评估是否建仓/试仓。\n"
            "· 意图按浪型：build/side→new_base(新开底仓)；defense/exit→不新建；t_only→probe(≤3%小仓试盘)，**但若 mainline_gate.confirmed_candidate 非空(当前主线已确认, 如农业): 不受 probe 限制, 可按 P3 new_base 建底仓(单票≤5%/合计≤10%, GJD撤退降级0.5时自动减半), 买点仍须狼大低吸(日内回撤≥2.5%或触前低+缩量+站回黄线)不追高**；watch/reserve 主题 t_only 仍只做T。\n"
            "· 建仓/试仓时，check_entry_filters 与 calc_position 必须显式传 mainline_dir=True（否则 side 浪型下 P3 会硬拦 new_base）。\n"
            "· 仍须满足狼大买点（日内回撤≥2.5% 或 触前低+缩量+站回黄线；或 计划/关键位命中），不因开盘放宽；P2 Gate(浪型/宏观)与 P3 三仓档位硬门仍生效。\n"
            + _base
        )
    return _base
def _check_drawdown(portfolio_json: str) -> tuple:
    """检查总回撤（峰值回撤），返回 (pct, blocked, reason)。

    公式：drawdown = (current_equity - peak_equity) / peak_equity
    current_equity 使用实时市值（total_asset_market），peak_equity 从文件追踪。
    """
    try:
        p = json.loads(portfolio_json)
        current_equity = p.get('total_asset_market', p.get('total_asset', 100000))
        peak_equity = p.get('peak_equity', max(current_equity, 100000))
        if peak_equity > 0:
            drawdown = (current_equity - peak_equity) / peak_equity
            if drawdown <= -0.05:
                return drawdown * 100, True, (
                    f"总回撤 {drawdown*100:.1f}% 已达 5% 硬止损线 "
                    f"(当前权益 {current_equity:.0f} / 峰值 {peak_equity:.0f})"
                )
            return drawdown * 100, False, ""
    except Exception:
        pass
    return 0.0, False, ""


def _check_consecutive_losses() -> int:
    """从 PostgreSQL paper_trades 查询连续亏损笔数（最近卖出交易的 profit 字段）"""
    try:
        from app.database import SessionLocal
        from app.models.paper_trade import PaperTrade

        db = SessionLocal()
        try:
            rows = db.query(PaperTrade.profit).filter(
                PaperTrade.account_id == 'stock',
                PaperTrade.direction == '卖出',
                PaperTrade.volume > 0,
                (PaperTrade.voided == 0) | (PaperTrade.voided == None)
            ).order_by(PaperTrade.created_at.desc()).limit(10).all()
        finally:
            db.close()

        count = 0
        for r in rows:
            if r.profit is not None and r.profit < 0:
                count += 1
            else:
                break
        return count
    except Exception:
        return 0


def _call_pi(prompt: str, task_id: str, timeout: int = 600) -> dict:
    """调用 Pi Server /chat 端点，返回 {reply, elapsed_ms, session_id, http_status}"""
    pi_url = _get_pi_server_url()
    # 每次执行使用唯一 session，避免复用缓存 agent 的脏状态
    session_id = f"pi_trade_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    payload = json.dumps({
        "message": prompt,
        "session_id": session_id,
        "mode": "trade",
    }).encode("utf-8")

    req = urllib.request.Request(
        pi_url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
            raw_body = resp.read().decode("utf-8")
            elapsed = (time.time() - t0) * 1000
            data = json.loads(raw_body)
            return {
                "reply": data.get("reply", ""),
                "elapsed_ms": data.get("elapsed_ms", int(elapsed)),
                "session_id": session_id,
                "http_status": resp.status,
            }
    except urllib.error.HTTPError as e:
        elapsed = (time.time() - t0) * 1000
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        logger.error(
            f"[Pi] HTTP {e.code} from {pi_url} ({elapsed:.0f}ms)\n"
            f"     session={session_id}\n"
            f"     body={error_body[:500]}"
        )
        raise  # re-raise to be caught by node_call_pi_decision
    except Exception as e:
        elapsed = (time.time() - t0) * 1000
        logger.error(
            f"[Pi] 连接失败 {pi_url} ({elapsed:.0f}ms): {e}\n"
            f"     session={session_id}"
        )
        raise


def _parse_signal(reply: str) -> tuple:
    m = re.search(
        r'SIGNAL:\s*(green|yellow|red)\s+POSITION:\s*(\d+)\s*REASON:\s*(.+)',
        reply, re.IGNORECASE,
    )
    if m:
        return m.group(1).lower(), int(m.group(2)), m.group(3).strip()
    return 'yellow', 60, ''


def _update_strategy_chain(stance: str, limit: int, reason: str, execution_id: str):
    try:
        from core.utils.strategy_chain import StrategyChain
        StrategyChain().set_pi_confirmation(stance=stance, position_limit=limit, reason=reason)
        logger.info(f"[{execution_id}] StrategyChain: {stance} limit={limit}%")
    except Exception as e:
        logger.error(f"[{execution_id}] StrategyChain 更新失败: {e}")


def _remove_bought_from_pool(reply: str, execution_id: str):
    try:
        from app.services.candidate_pool import get_candidate_pool
        pool = get_candidate_pool()
        bought = set()
        for m in re.finditer(
            r'(?:买入|建仓|加仓|已建仓).*?[（(]?(SH|SZ|BJ)(\d{6})[)）]?', reply
        ):
            bought.add(f"{m.group(2)}.{m.group(1)}")
        for sym in bought:
            pool.mark_promoted(sym)
            logger.info(f"[{execution_id}] [CandidatePool] Promoted {sym}")
    except Exception:
        pass


def _save_trade_report(task_id: str, execution_id: str, reply: str,
                       stance: str, limit: int, reason: str):
    try:
        log_dir = _get_workspace() / "memory" / "trade-reports"
        log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')
        with open(log_dir / f"{today}-trades.jsonl", 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "task_id": task_id, "execution_id": execution_id,
                "stance": stance, "position_limit": limit, "reason": reason,
                "report": reply,
            }, ensure_ascii=False) + '\n')
    except Exception as e:
        logger.error(f"[{execution_id}] 报告保存失败: {e}")


def _check_position_utilization(execution_id: str, position_limit: int, reason: str, stance: str):
    """仓位利用率检测：Pi 建议仓位 vs 实际持仓市值占比，脱节时告警"""
    try:
        portfolio_str = _read_portfolio()
        p = json.loads(portfolio_str)
        total_asset_market = p.get('total_asset_market', p.get('total_asset', 100000))
        market_value = p.get('market_value', p.get('total_cost', 0))
        actual_pct = (market_value / total_asset_market * 100) if total_asset_market > 0 else 0

        if position_limit > 0 and actual_pct < position_limit * 0.3 and position_limit >= 20:
            utilization = actual_pct / position_limit * 100
            logger.warning(
                f"[{execution_id}] [仓位利用率] Pi建议{position_limit}% "
                f"实际{actual_pct:.1f}%（利用率{utilization:.0f}%）"
            )
            from core.utils.strategy_chain import StrategyChain
            StrategyChain().set_pi_confirmation(
                stance=stance, position_limit=position_limit,
                reason=f"{reason} | ⚠️ 仓位利用率仅{utilization:.0f}%",
            )
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════
# 图节点
# ═══════════════════════════════════════════════════════════

def _read_main_line_context() -> str:
    """read main_line_state -> context block for trade prompt."""
    try:
        import json as _json
        path = os.getenv("MAIN_LINE_STATE_FILE", os.path.join(os.environ.get("DATA_DIR", "data"), "main_line_state.json"))
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            st = _json.load(f)
        ml = st.get("main_line") or "未知"
        cands = st.get("candidates") or []
        cat = st.get("catalyst") or {}
        cat_str = chr(44).join(f"{k}={v}" for k,v in cat.items() if v is not None)
        block = ("## 主线判定（main_line_state）" + chr(10)
                 + "- 当前主线：" + str(ml) + chr(10)
                 + "- 候选集（观察不建仓）：" + (", ".join(cands) if cands else "无") + chr(10)
                 + "- catalyst_score：" + (cat_str or "无") + chr(10))
        # 融合分(fusion: 研报0+资金0.3+强度0.2+净流入集中度0.5+银行, 周判方向可信度)
        fusion = st.get("fusion") or {}
        if fusion:
            top = sorted(fusion.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))[:3]
            fus_str = "、".join(f"{k}({v.get('score')})" for k, v in top)
            block += ("- 融合分 TOP3（资金/集中度主导，研报辅助）：" + fus_str + chr(10))
        # 2026-09-09 主线门(gate)权威摘要 + 主线浪型 + 农业全子概念确认(防前5截断)
        g = st.get("mainline_gate") or {}
        if g:
            gth = g.get("themes") or []
            conf_l = g.get("confirmed_candidate") or []
            lines = []
            for t in gth:
                wv = t.get("wave_structure")
                lines.append("- " + str(t.get("theme")) + " [" + str(t.get("verdict")) + " gate=" + str(t.get("gate"))
                             + " 结构比例=" + str(t.get("structure_ratio")) + "]" + ((" ｜ " + wv) if wv else ""))
            block += ("## 主线门（mainline_gate 权威判定——主线方向以此为准）" + chr(10)
                      + "- confirmed_candidate(可建仓主线)：" + (", ".join(conf_l) if conf_l else "无") + chr(10)
                      + chr(10).join(lines) + chr(10) + chr(10))
            # 农业全子概念个股确认概览(读 stock_confirm_result 全部子概念, 不截断前5)
            try:
                scp = os.path.join(os.environ.get("DATA_DIR", "data"), "stock_confirm_result.json")
                if os.path.exists(scp):
                    sc = _json.load(open(scp, encoding="utf-8"))
                    sc_ok = []
                    for k, v in sc.items():
                        if isinstance(v, dict) and v.get("theme") in conf_l and (v.get("confirm") or 0) > 0:
                            sc_ok.append(k + "=" + str(v.get("confirm")) + "/" + str(v.get("n")))
                    if sc_ok:
                        block += ("- 主线内个股已确认(突破/站稳)子概念: " + ", ".join(sc_ok) + chr(10))
                    else:
                        block += ("- 主线内个股确认: 当前 0 只突破站稳(成分多在回调/止跌中) —— 属买点未触发, 不否定主线方向, "
                                  + "启用已确认主线回调低吸模式(254 等 dip_prev_low), 非放弃建仓。" + chr(10) + chr(10))
            except Exception:
                pass
        block += ("- 【狼大主线准则③·产业链形态】判断某方向是否为狼大主线：须能拆上中下游/软硬、构成完整产业链。"
                  "请用 get_concept_mapping(主题) 拉全成分股，检查其是否覆盖 上游(材料/芯片/设备)→中游(制造/集成)→下游(应用/终端/服务) 或 软硬两端；"
                  "只覆盖单一环节(如仅下游应用) → 产业链形态不完备，主线可信度下调；覆盖完整上中下游 → 才按主线看待。"
                  "该判断用于主线判定与“去弱留强”，由你(交易agent)用工具自行分析，无需依赖外部产业链数据。" + chr(10))
        block += ("- 决策：候选->观察(等确认)；候选+当日动量/资金转强(RS转正/突破前高/fund_acc>0/均线多头)->确认->可对主线方向建仓；"
                  + "周判主线为方向参考，日内执行仍以实时概念TOP10双榜+浪型gate为准。" + chr(10) + chr(10))
        return block
    except Exception:
        return ""


def _read_position_context() -> str:
    """read position_class_result -> 概念高低位 context（风险定位: 高位应减/低位埋伏清单）"""
    try:
        import json as _json
        from collections import Counter as _Counter
        path = os.path.join(os.environ.get("DATA_DIR", "data"), "position_class_result.json")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            res = _json.load(f)
        vals = [v for v in res.values() if isinstance(v, dict) and v.get("action")]
        act = _Counter(v.get("action") or "?" for v in vals)
        reduce_list = sorted([v for v in vals if v.get("action") in ("减仓/只做T", "防御清仓")],
                             key=lambda v: -(v.get("fund_flow", {}).get("strength") or 0))[:5]
        buy_list = sorted([v for v in vals if v.get("action") in ("低吸埋伏", "回踩低吸")],
                          key=lambda v: -(v.get("fund_flow", {}).get("strength") or 0))[:5]
        act_str = "、".join(f"{k}={v}" for k, v in act.most_common(5))
        block = ("## 概念高低位（position_class）" + chr(10)
                 + "- 操作分布：" + (act_str or "无") + chr(10)
                 + "- 高位应减/只做T TOP：" + ("、".join(v.get("name", "?") for v in reduce_list) or "无") + chr(10)
                 + "- 低位可埋伏/低吸 TOP：" + ("、".join(v.get("name", "?") for v in buy_list) or "无") + chr(10)
                 + "- 决策：高位+资金流出→减仓/只做T；低位+资金流入+逻辑→埋伏候选(需确认链)；高低位为风险定位工具，不单独构成买卖信号。" + chr(10) + chr(10))
        return block
    except Exception:
        return ""


def _read_stock_confirm_context() -> str:
    """read stock_confirm_result -> 主线概念成分股确认比例(个股层确认: 三层联动之三)"""
    try:
        import json as _json
        path = os.path.join(os.environ.get("DATA_DIR", "data"), "stock_confirm_result.json")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            res = _json.load(f)
        if not res:
            return ""
        # 2026-09-09: 主线(gate confirmed)过滤+全子概念展示(防前5/前9截断漏生猪/肉鸡)
        import json as _json2
        conf_themes = []
        try:
            mlp = os.path.join(os.environ.get("DATA_DIR", "data"), "main_line_state.json")
            if os.path.exists(mlp):
                mst = _json2.load(open(mlp, encoding="utf-8"))
                gg = mst.get("mainline_gate") or {}
                conf_themes = gg.get("confirmed_candidate") or ([mst.get("main_line")] if mst.get("main_line") else [])
        except Exception:
            pass
        lines = []
        active_codes = []   # stage=突破候选/确认 的个股(子方向领涨激活, 2026-09-07: 益民600824/爱施德002416被比例遮住漏买)
        total_n = 0; total_c = 0
        for cname, v in res.items():
            if not isinstance(v, dict) or "confirm" not in v:
                continue
            th = v.get("theme") or ""
            if conf_themes and th and th not in conf_themes:
                continue
            total_n += v.get("n", 0); total_c += v.get("confirm", 0)
            pct = int(100 * (v.get("confirm") or 0) / max(v.get("n") or 1, 1))
            lines.append(f"{(th + '·') if th else ''}{cname}: 确认{v.get('confirm')}/{v.get('n')}({pct}%)")
            stocks = v.get("stocks") or []
            act = [str(s.get("code", "")).split(".")[0] for s in stocks
                   if (s.get("stage") or "") in ("确认", "突破候选")]
            if act:
                active_codes.append(f"{(th + '·') if th else ''}{cname}({'/'.join(act)})")
        overall = int(100 * total_c / max(total_n, 1)) if total_n else 0
        block = ("## 个股确认链（主线 confirmed 全子概念, 2026-09-09 口径）" + chr(10)
                 + "- 主线概念成分股确认：")
        for ln in lines[:30]:
            block += ln + "；"
        block = block.rstrip("；") + chr(10)
        block += ("- 总确认比例：" + str(overall) + "%" + chr(10)
                  + "- 活跃个股(突破候选/确认，子方向领涨，可纳入做T/低吸观察——勿因整组比例低而漏买)：" + (chr(44).join(active_codes[:10]) or "无") + chr(10))
        block += ("- 决策：成分股确认比例低(<30%)=主线未确认主升→限制重仓(等放量突破站稳)；高(≥50%)=主线确认→可沿主线建仓；"
                  + "整组比例低但出现'活跃个股'时：该子方向已局部激活，活跃个股可做T/轻仓低吸(重仓仍等整组确认)。" + chr(10) + chr(10))
        # 2026-09-07 wave 调档: 多主线候选资源分配提示(回测验证 wave-tuned-v2)
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps"))
            from main_line.wave_alloc import read_wave_alloc
            a = read_wave_alloc()
            if a.get("operation"):
                w = a["w"]; invest = a["invest"]
                block += ("## 主线候选权重分配(wave调档, 2026-09-07 回测落地)" + chr(10)
                          + f"- 当前wave={a['operation']} → TOP3权重 {int(100*w[0])}/{int(100*w[1])}/{int(100*w[2])}，仓位投入 {int(100*invest)}%"
                          + f"；TOP3=" + (chr(44).join(f"{k}({s})" for k, s in zip(a['top3'], a['scores'])) or "无") + chr(10)
                          + "- 决策：build→分散吃轮动；t_only→主第1(70%)+2/3试仓；side→主第1(60%)；defense→仅30%仓参与(不追突破, 回踩低吸)；exit→70%仓(只降不空)。"
                          + chr(10) + chr(10))
        except Exception:
            pass
        return block
    except Exception:
        return ""


def _read_confirm_context() -> str:
    """read position_class_result confirm_chain -> LOW 埋伏确认状态（确定性门槛: 候选 vs 可执行）"""
    try:
        import json as _json
        from collections import Counter as _Counter
        path = os.path.join(os.environ.get("DATA_DIR", "data"), "position_class_result.json")
        if not os.path.exists(path):
            return ""
        with open(path, encoding="utf-8") as f:
            res = _json.load(f)
        vals = [v for v in res.values() if isinstance(v, dict) and v.get("action") and v.get("position") == "LOW"]
        if not vals:
            return ""
        # 拥挤无空间过滤(双维: 真实基金拥挤×位置空间, rotation_universe.proxies.crowded_top) → 落到实处
        avoid = []
        ru = None
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps"))
            from main_line import rotation_universe as _ru
            ru = _ru
            avoid = (_ru.proxies().get("crowded_top") or [])
        except Exception:
            pass
        def in_avoid(nm):
            if not avoid or not ru:
                return False
            return any(any(k in str(nm) for k in ru.get_sub_universe().get(sub, [])) for sub in avoid)
        idx_cc = vals[0].get("signals", {}).get("index_confirm", "未知")
        stages = _Counter((v.get("confirm_chain") or {}).get("stage", "?") for v in vals)
        executable = [v["name"] for v in vals if not in_avoid(v["name"]) and (v.get("confirm_chain") or {}).get("stage") in ("确认", "突破候选")]
        candidate = [v["name"] for v in vals if not in_avoid(v["name"]) and (v.get("confirm_chain") or {}).get("stage") in ("缩量止跌", "结构到位")]
        excluded = sum(1 for v in vals if in_avoid(v["name"]))
        block = ("## 低位确认链（确定性门槛）" + chr(10)
                 + "- LOW 概念数：" + str(len(vals)) + "；指数确认状态：" + str(idx_cc) + chr(10)
                 + "- 确认链分布：" + ("、".join(f"{k}={v}" for k, v in stages.most_common(4)) or "无") + chr(10)
                 + "- 已确认可执行（突破/站稳）：" + ("、".join(executable[:5]) or "无") + chr(10)
                 + "- 埋伏候选（等确认链）：" + ("、".join(candidate[:5]) or "无") + chr(10))
        if excluded > 0:
            block += "- 拥挤无空间已剔除(真实基金拥挤×位置空间)：" + ("、".join(avoid) or "") + f"，剔除 LOW 候选 {excluded} 个 → 回避新建" + chr(10)
        block += "- 决策：指数确认未触发→LOW 不重仓；已确认(突破候选/确认)→可低吸；仅缩量止跌/结构到位→埋伏候选等待。" + chr(10) + chr(10)
        return block
    except Exception:
        return ""


_ROT_VERDICT_GUIDE = {
    "mainline_rotation": "允许：只做主线内细分轮动/补涨（候选须属当前主线，龙头未死）",
    "switch_low": "允许：防御性切低——候选须相对主线低位(rel=low)+资金流入+无2根孕线/未放量破前日低",
    "defensive_reduce": "允许：降个股/转ETF或埋伏rel-low候选，不追高",
    "sell_guard": "警示：持仓高位破位/资金流出——撤A，不做同板块低切补涨",
    "block": "禁止：主线外切低/切防御第二线；主线内可继续轮动/做T（build 主升以主线内细分轮动为主，≠主线禁买；与浪型gate一致）",
    "defense_mainline_rotation": "允许：defense期主线内'未出货链'资金调仓（如海外链→国算）",
    "manual_review": "人工：当前无明确轮动信号，等盘面",
}

def _read_risk_context() -> str:
    """风控门控(risk_gate v1)软约束：读 risk_flags DB → 持仓/候选按 symbol 判 block/review/reduce，
    并给 Pi 明确指令：建仓前命中 block/review 不买。"""
    NL = chr(10)
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "apps"))
        from main_line import risk_gate as _rg
        block = ("## 风控门控（risk_gate）" + NL
                 + "- 指令：建仓/加仓前必须检查标的 risk_flags（来源 forecast/express/ST/公告AI）；"
                   "命中 block（业绩雷/ST/立案/重组终止/监管）→ 禁止买入；review（财报窗口未落地/拥挤无空间）→ 等确认或人工；reduce（两融高位查杠杆）→ 系统性减仓。" + NL)
        try:
            import psycopg2 as _pg
            conn = _pg.connect(_os.environ.get("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading"))
            cur = conn.cursor()
            cur.execute("SELECT symbol FROM paper_positions WHERE account_id='stock'")
            syms = sorted({r[0] for r in cur.fetchall() if r[0]})
            cur.close(); conn.close()
        except Exception:
            syms = []
        if syms:
            lines = []
            for s in syms[:8]:
                try:
                    d = _rg.decide({"symbol": s})
                    if d["decision"] != "allow":
                        lines.append("%s → %s（%s）" % (s, d["decision"], d["reason"][:80]))
                except Exception:
                    continue
            if lines:
                block += "- 当前持仓风险：" + "；".join(lines) + NL
            else:
                block += "- 当前持仓风险：无（allow）" + NL
        try:  # 系统性风险联动开关(systemic_risk.json, 由行情采集写入)
            import json as _json
            _p3 = _os.path.join(_os.environ.get("DATA_DIR", "data"), "systemic_risk.json")
            if _os.path.exists(_p3):
                sr = _json.load(open(_p3, encoding="utf-8"))
                if sr.get("level", 0) > 0:
                    block += "- 系统性风险开关：level=%s — %s" % (sr.get("level"), (sr.get("advice") or "")) + NL
                    for a in (sr.get("alerts") or []):
                        block += "  · " + a + NL
        except Exception:
            pass
        return block + NL
    except Exception as e:
        return "## 风控门控（risk_gate）" + NL + "- 计算失败：" + str(e)[:80] + NL + NL

def _read_macro_context() -> str:
    """宏观/机构开关(macro_state v2)：读 macro_state.json → Wolf 四类开关文本。
    未生成/未含开关时给数值摘要并提示待 macro_state 刷新。"""
    NL = chr(10)
    try:
        import os as _os, json as _json
        p = _os.path.join(_os.environ.get("DATA_DIR", "data"), "macro_state.json")
        if not _os.path.exists(p):
            return "## 宏观/机构开关（macro_state）" + NL + "- 尚未生成 macro_state.json，跳过宏观上下文。" + NL + NL
        st = _json.load(open(p, encoding="utf-8"))
        txt = st.get("macro_switches_text") or ""
        y = st.get("yields") or {}; dxy = st.get("dxy") or {}; m = st.get("market") or {}; g = m.get("gjd") or {}
        cn = (y.get("cn") or {}); us = (y.get("us") or {})
        head = ("## 宏观快照" + NL
                + "- 收益率: CN30=%(cn30)s CN10=%(cn10)s | US30=%(us30)s US10=%(us10)s | DXY=%(dxy)s" % {
                    "cn30": cn.get("30年"), "cn10": cn.get("10年"), "us30": us.get("30年"), "us10": us.get("10年"),
                    "dxy": dxy.get("value")})
        margin = ("- 两融: 余额=%(rzrqye)s亿 20d=%(chg)s%% 净买=%(net)s亿" % {
            "rzrqye": m.get("margin_rzrqye"), "chg": m.get("margin_20d_chg"), "net": m.get("margin_net_buy")}) if m.get("margin_rzrqye") is not None else "- 两融: 无数据"
        gjd = ("- GJD: HS300份额20d=%(h300)s%% 上证50份额20d=%(h50)s%% 5d流入=%(in)s亿" % {
            "h300": g.get("sh300_chg20"), "h50": g.get("sh50_chg20"), "in": g.get("sh300_inflow_5d")}) if g else "- GJD: 无数据"
        block = head + NL + margin + NL + gjd + NL
        if txt:
            block += txt
        else:
            block += "## 宏观/机构开关（Wolf v2）" + NL + "- macro_state 未含开关推导(请跑 build_macro_state v2)" + NL
        return block + NL
    except Exception as e:
        return "## 宏观/机构开关（macro_state）" + NL + "- 读取失败：" + str(e)[:80] + NL + NL


def _read_rotation_gate_context() -> str:
    """轮动门控（rotation_gate v2）上下文：wave_state op + main_line 吸金 → gate 判定。
    与 docs/p2-rotation-validation-analysis.md gate_rotation v2 同源；供 Pi 决定'能否切低/是否只做主线内轮动'。
    当前 rotation_healthy 未接细分宇宙实时模块，默认健康(True)。"""
    NL = chr(10)
    try:
        import sys as _sys, json as _json, os as _os
        DATA = _os.environ.get("DATA_DIR", "data")
        rg = None
        for _p in (_os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "..", "..", "apps"),
                   "/app/apps/main_line"):
            try:
                if _p not in _sys.path: _sys.path.insert(0, _p)
                from main_line import rotation_gate as _rg
                rg = _rg
                break
            except Exception:
                continue
        if rg is None:
            return "## 轮动门控（rotation_gate）" + NL + "- rotation_gate 模块未加载，跳过。" + NL + NL
        wl = _wave_level_gate()
        op = wl.get("operation") or ""
        ml_name = "?"; sucking = None; healthy = None
        crowded = []; room = []; crep = []; holdT = []
        # 细分宇宙+拥挤度代理(rotation_universe.py)：真实子方向资金/相对低位
        try:
            from main_line import rotation_universe as _ru
            _p = _ru.proxies()
            if _p and _p.get("rotation_healthy") is not None:
                sucking = bool(_p.get("mainline_sucking"))
                healthy = bool(_p.get("rotation_healthy"))
                crowded = _p.get("crowded_top") or []
                room = _p.get("room_bottom") or []
                crep = _p.get("crowded_represent") or []
                holdT = _p.get("holdT_top") or []
        except Exception:
            pass
        try:
            _p2 = _os.path.join(DATA, "main_line_state.json")
            if _os.path.exists(_p2):
                ml = _json.load(open(_p2, encoding="utf-8"))
                ml_name = ml.get("main_line") or "?"
                if sucking is None:  # fusion 兜底代理
                    fu = (ml.get("fusion") or {}).get(ml_name) or {}
                    score = float(fu.get("score") or 0); conc = float(fu.get("conc") or 0)
                    sucking = bool(score >= 0.85 and conc >= 0.7)
        except Exception:
            pass
        if sucking is None: sucking = False
        if healthy is None: healthy = True
        # 主线内判定: 有明确主线 -> inside_mainline=True, 使 build 抽血期得到"主线内细分轮动"而非"禁切出主线/block"
        inside_mainline = bool(ml_name and ml_name != "?")
        dec = rg.decide(op, mainline_sucking=sucking, inside_mainline=inside_mainline,
                        rotation_healthy=healthy)
        verdict = dec.get("verdict", "block")
        reason = dec.get("reason", "")
        guide = _ROT_VERDICT_GUIDE.get(verdict, "")
        block = ("## 轮动门控（rotation_gate）" + NL
                 + f"- 浪型操作：{op or '未知'} ｜ 主线：{ml_name}" + NL
                 + f"- 主线吸金/轮动健康（细分宇宙代理）：吸金={'是' if sucking else '否'} 健康={'是' if healthy else '否'}" + NL)
        if crowded:
            block += f"- 拥挤无空间(回避新建/减仓)：{'、'.join(crowded[:4])}" + NL
        if room:
            block += f"- 可埋伏(低拥挤+有空间)：{'、'.join(room[:4])}" + NL
        if holdT:
            block += f"- 拥挤但有空间(做T积累/等回调，勿新建重仓)：{'、'.join(holdT[:4])}" + NL
        if crep:
            block += f"- 高拥挤代表个股(芯片/光通信,真实公募持仓)：{'、'.join(crep[:6])}" + NL
        # 主线内相对强度排序/去弱留强/龙头识别 (2026-09-03 P2 补齐, 软指导)
        strong = room or []; mid = holdT or []; weak = crowded or []
        leader = (strong or mid or [ml_name])[0] if (strong or mid) else (ml_name or '?')
        block += f"- 主线内相对强度(去弱留强)：留强={'、'.join(strong[:3]) or '无'} ｜ 中={'、'.join(mid[:3]) or '无'} ｜ 去弱={'、'.join(weak[:3]) or '无'}" + NL
        block += f"- 主线龙头候选：{leader}" + NL
        block += f"- 去弱留强指令：主线内优先保留『资金流入+低位有空间(room)』子方向，规避『高位拥挤无空间(crowded_top)』；去弱=减持/规避 crowded 方向，留强=低吸 room 方向。" + NL
        block += f"- 判定：{verdict} —— {guide}" + NL
        if reason:
            block += f"- 依据：{reason[:160]}" + NL
        return block + NL
    except Exception as e:
        return "## 轮动门控（rotation_gate）" + NL + "- 计算失败：" + str(e)[:80] + NL + NL

def _read_rotation_switch_context() -> str:
    """主线内切换·个股级计划（rotation_switch）上下文：读 data/rotation_switch_plan.json
    （15:25 dry-run/自动执行产物；SWITCH_EXEC_ENABLED=1 时含 sell_plan/buy_chains/exec_results）。
    与 rotation_gate 判定互补：gate 说能不能切，本块说当前建议切谁/买哪条链。"""
    NL = chr(10)
    try:
        import json as _j, os as _os
        p = _os.path.join(_os.environ.get("DATA_DIR", "data"), "rotation_switch_plan.json")
        stale = True
        if _os.path.exists(p):
            try:
                _pl = _j.load(open(p, encoding="utf-8"))
                stale = str(_pl.get("ts") or "")[:10] != __import__("datetime").date.today().isoformat()
            except Exception:
                stale = True
        if not _os.path.exists(p) or stale:
            # 无当日独立计划：退回链信号上下文，由 Pi(auto_trade) 在既有链路内做切换决策
            try:
                _ru = _j.load(open(_os.path.join(_os.environ.get("DATA_DIR", "data"), "rotation_universe_result.json"), encoding="utf-8"))
                return ("## 主线内切换计划（rotation_switch）" + NL
                        + "- 状态：当日无独立 Agent 计划，由 auto_trade/Pi 按以下链信号决策（不新增下单通道）" + NL
                        + "- 拥挤无空间：" + "、".join((_ru.get("crowded_top") or [])[:4]) + NL
                        + "- 可埋伏/room：" + "、".join((_ru.get("room_bottom") or [])[:4]) + NL
                        + "- 拥挤但有空间(做T积累)：" + "、".join((_ru.get("holdT_top") or [])[:4]) + NL
                        + "- 轮动健康：" + ("是" if _ru.get("rotation_healthy") else "否") + " ｜ 主线抽血：" + ("是" if _ru.get("mainline_sucking") else "否") + NL
                        + "- 双线门(狼大)：主线内 room 可切；防御/资源第二线仅在非build(t_only/side/defense/exit) 且 rotation_healthy 且非抽血时允许，主升明牌吸金期只做主线内" + NL + NL)
            except Exception:
                pass
            return ""
        pl = _j.load(open(p, encoding="utf-8"))
        mode = pl.get("mode") or "?"
        wave = pl.get("wave") or "?"
        block = ("## 主线内切换计划（rotation_switch）" + NL
                 + f"- 模式：{mode} ｜ 浪型：{wave}" + NL)
        sp = pl.get("sell_plan") or []
        if sp:
            for it in sp[:6]:
                block += (f"- 卖建议：{it.get('symbol')} action={it.get('action')} "
                          f"chain={','.join(it.get('chains') or [])[:40]} signal={it.get('signal')}" + NL)
        else:
            block += "- 卖建议：无（当前持仓不在出货档）" + NL
        bc = pl.get("buy_chains") or []
        if bc:
            block += "- 可埋伏买链：" + "、".join(x.get("chain", "") for x in bc[:4]) + "（需属主线才自动买）" + NL
        er = pl.get("exec_results") or []
        if er:
            block += "- 执行结果：" + "、".join("%s %s %s→%s" % (x.get("action"), x.get("symbol"), x.get("volume"), x.get("resp")) for x in er[:8]) + NL
        else:
            block += "- 执行结果：本次无自动成交" + NL
        return block + NL
    except Exception as e:
        return "## 主线内切换计划（rotation_switch）" + NL + "- 读取失败：" + str(e)[:80] + NL + NL


_OP_GUIDE = {
    "build": "可建仓/追主升：主线内低吸埋伏、波段持仓可加仓，避免追高杀跌",
    "t_only": "只做T不新建仓：底仓不动，T仓按分时T出/正T低吸/黄线离场纪律高抛低吸",
    "side": "观望/调仓换股：不追主升、不满仓，等结构确认后再动",
    "defense": "防御不建仓：等待企稳/止跌确认，规避C杀，只保留底仓",
    "exit": "兑现降仓：反弹即减、控制回撤，不再开新仓",
}

def _read_wave_context() -> str:
    """浪型级别上下文：优先注入 wave_agent 两级判定(level/sub_level/operation/gate)，
    与 _wave_level_gate 硬拦同源(读 wave_state.json)；文件缺失时回退 rule-based 结构判定。"""
    NL = chr(10)
    try:
        import json as _json, os as _os
        path = _os.path.join(_os.environ.get("DATA_DIR", "data"), "wave_state.json")
        if _os.path.exists(path):
            st = _json.load(open(path, encoding="utf-8"))
            wl = _wave_level_gate()   # {level, sub_level, operation, gate} 与安全门同源
            lvl = wl["level"] or "未知"
            sub = wl["sub_level"] or ""
            op = wl["operation"]
            gate = wl["gate"]
            conf = st.get("confidence")
            reasons = (st.get("reasons") or "").strip()
            if len(reasons) > 140:
                reasons = reasons[:140] + "…"
            date = st.get("date") or ""
            guide = _OP_GUIDE.get(op, "")
            block = ("## 浪型级别（wave_agent 两级判定）" + NL
                     + "- 大级别：" + lvl + (f" ｜ 子浪/局部：{sub}" if sub else "") + NL
                     + f"- 操作：{op}（gate={gate}）——" + guide + NL)
            if conf is not None:
                block += f"- 置信度：{conf}" + NL
            if date:
                block += f"- 判定日期：{date}" + NL
            if reasons:
                block += f"- 依据：{reasons}" + NL
            return block + NL
    except Exception:
        pass
    # 回退：rule-based 结构判定
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "apps"))
        from main_line import wave_level
        return wave_level.read_wave_context()
    except Exception:
        return ""


def _read_three_tier_context() -> str:
    """P3 三仓档位摘要（StepB dry-run）：按当前浪型档位说明可做/禁做与现金底线，注入 Pi prompt。
    与 check_entry_filters 的 three_tier_details 同源（config/p3_position_tiers.json）。"""
    NL = chr(10)
    try:
        from app.services.position_tier import three_tier_gate, tier_mode_enabled
        w = _wave_level_gate() or {}
        op = w.get("operation") or "side"
        def _cap(intent, has_base=False, tuni=False, main=True, rel=False):
            d = three_tier_gate(wave_state={"operation": op, "sub_level": w.get("sub_level") or "", "level": w.get("level") or ""},
                                intent=intent, has_base=has_base, t_universe=tuni,
                                rel_low=rel, mainline_dir=main)
            return ("✅≤%s%%" % d["cap_pct"]) if d["intent_allowed"] else "❌"
        mode = "硬拦(P3_TIER_MODE=1)" if tier_mode_enabled() else "dry-run只记录(P3_TIER_MODE=0)"
        sub = w.get("sub_level") or ""
        return ("## 三仓档位（P3 v0, %s）" % mode + NL
                + "- 当前浪型档位：%s%s" % (op, ("/%s" % sub) if sub else "") + NL
                + "- 新开底仓(new_base)：%s" % _cap("new_base", has_base=False, tuni=False, main=True) + NL
                + "- 已有底仓加厚(add_base)：%s" % _cap("add_base", has_base=True, tuni=True, main=True) + NL
                + "- T资格回补(refill_base，无仓但在T宇宙)：%s" % _cap("refill_base", has_base=False, tuni=True, main=True) + NL
                + "- T仓低吸(t_refill，已有底仓)：%s" % _cap("t_refill", has_base=True, tuni=True, main=True) + NL
                + "- 档位现金底线按 check_entry_filters/calc_position 输出执行" + NL + NL)
    except Exception:
        return ""


def _wave_norm(v):
    """归一化 level/sub_level 字段：小写、去空白、去下划线，保留连字符(4-4)。"""
    if v is None:
        return ""
    return str(v).strip().lower().replace("_", " ").replace(" ", "")

def _wave_operation(level, sub):
    """按狼大两级结构 (level 大级别 + sub_level 子浪/局部) 判 operation。
    operation: build(建仓追)/t_only(只做T)/side(观望调仓换股)/defense(防御不建仓)/exit(兑现降仓)。
    子浪/局部优先：取文本中【最早出现】的浪型 token（兼容规范 sub_level 与旧自由文本，如
    大4回调浪·4-3筑底段（未到4-4反弹）→ 取 4-3）。若子浪无命中则落级到大级别。"""
    import re as _re
    L = _wave_norm(level)
    S = _wave_norm(sub)
    # (正则, operation)：按优先映射；左起最早命中者生效
    token_rules = [
        (r"4-5", "defense"), (r"失败5", "defense"),
        (r"4-4", "t_only"),
        (r"4-3", "side"),
        (r"4-2", "t_only"), (r"b反", "t_only"),
        (r"4-1", "defense"),
        (r"c杀", "defense"),
        (r"abc", "side"),
        (r"双头", "exit"), (r"m顶", "exit"), (r"头肩", "exit"),
        (r"w底", "build"), (r"双底", "build"),
        (r"3-5", "exit"),
        (r"3-4", "t_only"),
        (r"3-3", "build"),
        (r"3-2", "side"),
        (r"3-1", "side"),   # 3-1起步段未确认前保守为side(观望/轻仓分批); 确认build需LLM op=build且confidence>=0.6(见 _wave_level_gate)
        (r"衰竭", "exit"),
    ]
    # 找文本中最早出现的浪型 token
    best = None  # (idx, op)
    for pat, op in token_rules:
        m = _re.search(pat, S)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), op)
    if best is not None:
        return best[1]
    # 子浪无命中：再对 level 本身做同样的左起 token 扫描（兼容旧版 level 就是 "4-5下杀" 等自由文本）
    best = None
    for pat, op in token_rules:
        m = _re.search(pat, L)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), op)
    if best is not None:
        return best[1]
    # ---- 落级到指数大级别 ----
    if any(k in L for k in ["d3", "主升", "大3", "上升", "大1", "反转", "d1"]):
        return "build"
    if any(k in L for k in ["d5", "末段", "5浪", "衰竭"]):
        return "exit"
    if any(k in L for k in ["d4", "大4", "4回"]):
        return "defense"
    if any(k in L for k in ["d2", "大2", "调整"]):
        return "side"
    if any(k in L for k in ["down", "下跌", "下跌一浪"]):
        return "defense"
    return "side"

_OP_TO_GATE = {"build": "normal", "t_only": "t_only", "side": "side", "defense": "defense", "exit": "exit"}

def _wave_level_gate() -> dict:
    """按狼大两级规则: 读 wave_state {level, sub_level}, 返回 {level, sub_level, operation, gate}。
    gate: normal(可建仓)/t_only(只做T)/side(观望调仓换股)/defense(防御不建仓)/exit(兑现降仓)。
    code层硬拦截仅在 defense/exit 触发；t_only/side 传给 Pi 提示只做T/调仓不追。"""
    try:
        import os as _os, json as _json
        path = _os.path.join(_os.environ.get("DATA_DIR", "data"), "wave_state.json")
        if not _os.path.exists(path):
            return {"level": "未知", "sub_level": "", "operation": "side", "gate": "normal"}
        st = _json.load(open(path, encoding="utf-8"))
        lvl = st.get("level") or "未知"
        sub = st.get("sub_level") or st.get("sublevel") or st.get("sub") or ""
        op = _wave_operation(lvl, sub)
        # 09-02 fix: 3-1默认保守(side); 仅当 agent 明确build 且 confidence>=0.6 才放行 build(避免未确认主升起步就追)
        _llm_op = (st.get("operation") or "").strip().lower()
        _conf = st.get("confidence")
        if op == "side" and _llm_op == "build" and isinstance(_conf, (int, float)) and _conf >= 0.6:
            op = "build"
        return {"level": _wave_norm(lvl), "sub_level": _wave_norm(sub), "operation": op, "gate": _OP_TO_GATE[op]}
    except Exception:
        return {"level": "未知", "sub_level": "", "operation": "side", "gate": "normal"}

def _read_wolf_t_context() -> str:
    """做T纪律(向狼大看齐): 正T/倒T规则 + 只做T/底仓/3-5点 纪律块, 注入 Pi prompt。"""
    try:
        from app.services.wolf_t_rules import discipline_context
        return discipline_context()
    except Exception:
        return ""


def _read_plan_context() -> str:
    """计划库触发检查(plan_runner): 每日/每窗口检查 armed 计划, 命中→fired + 注入指令给 Pi。"""
    try:
        from app.services.plan_runner import plan_context
        return plan_context()
    except Exception:
        return ""


def _read_discipline_context(window: Optional[str] = None) -> str:
    """狼大纪律规则(周末降仓+板上减半)上下文块: 注入 Pi prompt, 由 agent 结合实时行情执行。"""
    try:
        import json as _j, datetime as _dt
        from app.services.wolf_discipline import discipline_context
        pf = _read_portfolio()   # 持仓/现金/总资产 JSON
        return discipline_context(portfolio=pf, now=_dt.datetime.now(), window=window, quotes=None)
    except Exception:
        return ""


def _read_wolf_judge_context() -> str:
    """六因子规则打分(软指导, 不硬拦): 注入 Pi prompt 辅助"要不要出手"判断, 不伤召回. 见 wolf_judge."""
    try:
        from app.services.wolf_judge import day_gate_prompt
        return day_gate_prompt()
    except Exception:
        return ""


def node_fetch_context(state: TradeState) -> dict:
    """
    节点 1: 获取上下文 —— 确定性节点

    直接读取扫描报告、持仓、候选池、上一轮立场、市场结构、时段指令。
    这些数据之前由 Pi Agent 通过 tool-calling 获取，现在由代码层保证。
    """
    eid = state['execution_id']
    logger.info(f"[{eid}] [Graph] ▶ fetch_context")

    regime, label, suggestion = _read_market_regime()
    style_info = _read_style_regime()

    return {
        "scan_report_text": _read_scan_report(),
        "portfolio_json": _read_portfolio(),
        "pool_context": _read_pool_context(state['task_id']),
        "stance_context": _read_stance_context(),
        "regime_context": "",   # 2026-09-03: 不再注入震荡/趋势市场状态(该状态错误且与狼大浪型/主线打架)，大级别以 wave_context 为准
        "style_context": "",   # 2026-09-03: 不再注入平台风格模式(进攻/防御/资源避险)市场状态, 主线方向以 main_line_context 为准
        "market_regime": regime,
        "style_regime": style_info.get("style_regime", "NEUTRAL"),
        "trade_mode_instruction": _get_trade_instruction(state['window'], regime),
        "main_line_context": _read_main_line_context(),
        "position_context": _read_position_context(),
        "confirm_context": _read_confirm_context(),
        "stock_confirm_context": _read_stock_confirm_context(),
        "wave_context": _read_wave_context(),
        "three_tier_context": _read_three_tier_context(),
        "rotation_gate_context": _read_rotation_gate_context(),
        "rotation_switch_context": _read_rotation_switch_context(),
        "macro_context": _read_macro_context(),
        "risk_context": _read_risk_context(),
        "discipline_context": _read_discipline_context(state['window']),
        "plan_context": (_pc := _read_plan_context()),
        "plan_triggered": ('🔔 计划命中' in _pc),
        "wolf_t_context": _read_wolf_t_context(),
        "wolf_judge_context": _read_wolf_judge_context(),
    }


def node_check_safety_gates(state: TradeState) -> dict:
    """
    节点 2: 安全门检查 —— 确定性节点

    代码层硬风控，不依赖 LLM:
      1. 总回撤 ≥ 5% → 硬禁止一切买入
      2. 连续亏损 ≥ 3 笔 → 当日熔断
    """
    eid = state['execution_id']
    logger.info(f"[{eid}] [Graph] ▶ check_safety_gates")

    if SAFETY_GATE_BYPASS:
        logger.warning(f"[{eid}] [Graph] ⚠ 安全门已旁路 (SAFETY_GATE_BYPASS=True)，跳过回撤/熔断检查")
        return {
            "drawdown_pct": 0.0,
            "consecutive_losses": 0,
            "hard_blocked": False,
            "block_reason": "",
        }

    drawdown, blocked, reason = _check_drawdown(state['portfolio_json'])
    wl_gate = _wave_level_gate()
    if wl_gate["gate"] in ("defense", "exit"):
        logger.warning(f"[{eid}] [Graph] ⛔ 狼大浪型防御/兑现: {wl_gate['level']}/{wl_gate['sub_level']} op={wl_gate['operation']}，不建仓")
        blocked = True
        reason = f"狼大浪型={wl_gate['level']}+{wl_gate['sub_level']} op={wl_gate['operation']}（防御/兑现，不建仓）"
       
    consecutive = _check_consecutive_losses()

    updates = {
        "drawdown_pct": drawdown,
        "consecutive_losses": consecutive,
    }

    if blocked:
        updates["hard_blocked"] = True
        updates["block_reason"] = reason
        logger.warning(f"[{eid}] [Graph] ⛔ 回撤拦截: {reason}")
    elif consecutive >= 3:
        updates["hard_blocked"] = True
        updates["block_reason"] = f"连续亏损 {consecutive} 笔，触发熔断"
        logger.warning(f"[{eid}] [Graph] ⛔ 熔断拦截: 连亏{consecutive}笔")
    else:
        updates["hard_blocked"] = False
        updates["block_reason"] = ""
        logger.info(f"[{eid}] [Graph] ✓ 安全门通过 (回撤{drawdown:.1f}%, 连亏{consecutive})")

    return updates


def node_handle_blocked(state: TradeState) -> dict:
    """节点 2b: 拦截处理 —— 生成拦截报告，跳过 Pi 调用"""
    eid = state['execution_id']
    now = datetime.now()
    report = (
        f"## Marcus 交易报告 — {state['window']}\n\n"
        f"### ⛔ 交易被安全门拦截\n\n"
        f"**拦截原因**: {state['block_reason']}\n\n"
        f"| 检查项 | 结果 |\n"
        f"|--------|------|\n"
        f"| 总回撤 | {state['drawdown_pct']:.1f}% |\n"
        f"| 连续亏损 | {state['consecutive_losses']} 笔 |\n\n"
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )
    logger.warning(f"[{eid}] [Graph] ⛔ 已拦截: {state['block_reason']}")
    return {
        "report": report,
        "pi_stance": "red",
        "pi_position_limit": 0,
        "pi_reason": state['block_reason'],
        "pi_raw_reply": report,
        "error": state['block_reason'],
    }


def node_call_pi_decision(state: TradeState) -> dict:
    """
    节点 3: Pi 决策 —— LLM 节点

    将所有预获取数据打包为结构化 Prompt 发送 Pi Server。
    Pi 负责：分析 → 选股 → check_entry_filters → calc_position → 下单 → 报告.
    """
    eid = state['execution_id']
    logger.info(f"[{eid}] [Graph] ▶ call_pi_decision")

    now = datetime.now()

    # 截断扫描报告（2000 字足够）
    scan = state['scan_report_text']
    if len(scan) > 2000:
        scan = scan[:2000] + '\n... (已截断)'

    _plan_banner = ("🔔 **计划触发执行**：请按下方已命中的计划立即行动，优先级高于常规窗口指令。\n\n" if state.get('plan_triggered') else "")
    prompt = (
        f"{_plan_banner}{state.get('plan_context', chr(39)+chr(39))}"
        f"{state.get('wolf_t_context', chr(39)+chr(39))}"
        f"{state.get('wolf_judge_context', chr(39)+chr(39))}"
        f"{state.get('discipline_context', chr(39)+chr(39))}"
        f"{state.get('wave_context', chr(39)+chr(39))}"
        f"{state.get('three_tier_context', chr(39)+chr(39))}"
        f"{state.get('main_line_context', chr(39)+chr(39))}"
        f"{state.get('position_context', chr(39)+chr(39))}"
        f"{state.get('confirm_context', chr(39)+chr(39))}"
        f"{state.get('stock_confirm_context', chr(39)+chr(39))}"
        f"{state.get('rotation_gate_context', chr(39)+chr(39))}"
        f"{state.get('rotation_switch_context', chr(39)+chr(39))}"
        f"{state.get('macro_context', chr(39)+chr(39))}"
        f"{state.get('risk_context', chr(39)+chr(39))}"
        f"{state['regime_context']}\n"
        f"{state.get('style_context', '')}"
        f"{state['pool_context']}"
        f"{state['trade_mode_instruction']}\n"
        f"{state['stance_context']}"
        f"\n━━━ 系统已预获取的数据（无需重复调用工具）━━━\n\n"
        f"## 最新扫描报告\n```json\n{scan}\n```\n\n"
        f"## 当前账户持仓\n```json\n{state['portfolio_json']}\n```\n\n"
        f"请立即执行以下操作：\n"
        f"1. 分析上方已提供的扫描报告和持仓数据\n"
        f"2. 按当前市场结构对应的策略参数选股分析"
        f"（可调用 check_entry_filters / calc_position / get_quote / "
        f"get_concept_fund_flow / get_realtime_indicators / get_technical / get_intraday_min 等）\n"
        f"3. 执行交易（买入/卖出/调仓）\n"
        f"4. 输出完整交易报告（含 SIGNAL 行）\n\n"
        f"你是 Marcus 右侧交易专家。基础数据已就绪，请直接分析和决策。\n"
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')}"
    )

    try:
        pi_result = _call_pi(prompt, state['task_id'])
    except Exception as e:
        logger.error(f"[{eid}] [Graph] Pi 调用异常: {e}")
        return {"error": f"Pi Server 调用失败: {e}"}

    reply = pi_result.get("reply", "")
    if not reply or reply == '(无回复)':
        diag = (
            f"Pi 未返回有效交易报告\n"
            f"  Pi Server: {_get_pi_server_url()}\n"
            f"  Session: {pi_result.get('session_id', 'N/A')}\n"
            f"  HTTP Status: {pi_result.get('http_status', 'N/A')}\n"
            f"  Server Elapsed: {pi_result.get('elapsed_ms', 'N/A')}ms\n"
            f"  Reply Preview: {reply[:200] if reply else '(empty)'}"
        )
        logger.error(f"[{eid}] [Graph] {diag}")
        return {"error": diag}

    logger.info(f"[{eid}] [Graph] ✓ Pi 回复 ({len(reply)} chars)")
    return {"pi_raw_reply": reply}


def node_process_result(state: TradeState) -> dict:
    """
    节点 4: 结果处理 —— 确定性节点

    解析 SIGNAL → 更新 StrategyChain → 移除候选池已买入标的 → 持久化报告 → 仓位利用率检测.
    """
    eid = state['execution_id']
    logger.info(f"[{eid}] [Graph] ▶ process_result")

    reply = state.get('pi_raw_reply', '')
    if not reply:
        return {"error": state.get('error', '无 Pi 回复可处理')}

    stance, position_limit, reason = _parse_signal(reply)

    _update_strategy_chain(stance, position_limit, reason, eid)
    _remove_bought_from_pool(reply, eid)
    _save_trade_report(state['task_id'], eid, reply, stance, position_limit, reason)
    _check_position_utilization(eid, position_limit, reason, stance)

    clean_report = re.sub(r'\n?SIGNAL:.*', '', reply).strip()

    logger.info(f"[{eid}] [Graph] ✓ process_result: {stance} limit={position_limit}%")
    return {
        "pi_stance": stance,
        "pi_position_limit": position_limit,
        "pi_reason": reason,
        "report": clean_report,
    }


def node_check_regime_compliance(state: TradeState) -> dict:
    """
    节点 3.5: 策略合规检查 —— 确定性节点

    检查 Pi 的决策是否符合当前市场结构的策略参数。
    震荡市下对仓位/工具/策略进行硬拦截，发现违规强制修正。
    """
    eid = state['execution_id']
    regime = state.get('market_regime', 'trend')
    reply = state.get('pi_raw_reply', '')

    # 狼大纪律: 周末降仓(周五, 不限 regime) —— 仓位占比达到阈值时只允许降T仓, 禁止买入/加仓/追
    try:
        import re as _re, datetime as _dt
        from app.services.wolf_discipline import weekend_de_risk
        _wd = weekend_de_risk(state.get('portfolio_json', '{}'), _dt.datetime.now(), window=state.get('window'))
        if _wd.get('active'):
            _rep = state.get('pi_raw_reply', '') or ''
            if _re.search(r'买入|加仓|建仓|追|进场', _rep):
                return {"regime_violation": True,
                        "regime_violation_reason": _wd['directive'] + "（周末降仓日：只允许降T仓、禁止买入/加仓/追）"}
    except Exception:
        pass

    # 狼大口径(2026-09-03 审计): 去掉平台自研"震荡市仓位≤50%/单票≤8%/必须60分/禁产业链建仓"硬约束。
    if regime == 'oscillation':
        logger.info(f"[{eid}] [Graph] ▶ check_regime_compliance (震荡/调整浪 -> 狼大口径: 只做T不追主升)")
        note = ("【狼大口径·震荡/调整浪】只做T、不追主升；主线低吸须满足狼大买点"
                "(日内回撤≥2.5%或触前低 + 缩量 + 站回黄线) 或 计划/关键位命中。"
                "六因子软指导与 wave_level_gate 已生效；不再套用平台仓位/单票/60分/产业链建仓硬限。")
        state['pi_raw_reply'] = (state.get('pi_raw_reply', '') + chr(10) + chr(10) + note)
        return {"regime_violation": False, "regime_violation_reason": note}
    logger.info(f"[{eid}] [Graph] ✓ 趋势市，跳过策略合规检查")
    return {"regime_violation": False, "regime_violation_reason": ""}

    logger.info(f"[{eid}] [Graph] ▶ check_regime_compliance (震荡市)")
    violations = []

    # 1. 检查仓位上限
    stance, position_limit, reason = _parse_signal(reply)
    if position_limit > 50:
        violations.append(
            f"仓位上限{position_limit}%超过震荡市上限50%，已强制修正为50%")
        # 修正回复中的 SIGNAL 行
        old_signal = f"POSITION:{position_limit}"
        new_signal = f"POSITION:50"
        state['pi_raw_reply'] = reply.replace(old_signal, new_signal)
        # 追加合规警告到报告末尾
        state['pi_raw_reply'] += (
            f"\n\n⚠️ [策略合规自动修正] 震荡市仓位上限从{position_limit}%修正为50%。"
        )

    # 2. 检查是否使用了日线策略（震荡市必须用60分钟）
    if '产业链建仓计划' in reply or '产业链建仓' in reply:
        violations.append("震荡市报告中出现「产业链建仓计划」→ 趋势市策略误用！")

    # 3. 检查是否调用了分钟线工具
    if 'get_intraday_min' not in reply and '下单' in reply:
        violations.append("震荡市执行买入但未调用 get_intraday_min → 未确认60分钟趋势！")

    # 4. 检查单票仓位是否超过8%（从报告中解析）
    buy_pcts = re.findall(r'买入.*?(\d+(?:\.\d+)?)%', reply)
    for pct_str in buy_pcts:
        pct = float(pct_str)
        if pct > 8:
            violations.append(f"震荡市单票仓位{pct}%超过8%上限")

    if violations:
        reason_str = "; ".join(violations)
        logger.warning(f"[{eid}] [Graph] ⚠️ 策略合规违规: {reason_str}")
        return {
            "regime_violation": True,
            "regime_violation_reason": reason_str,
        }
    else:
        logger.info(f"[{eid}] [Graph] ✓ 策略合规通过")
        return {"regime_violation": False, "regime_violation_reason": ""}


# ═══════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════

def _route_after_gates(state: TradeState) -> str:
    if state.get('hard_blocked', False):
        return "handle_blocked"
    return "call_pi_decision"


# ═══════════════════════════════════════════════════════════
# 图构建 & 公共 API
# ═══════════════════════════════════════════════════════════

_graph = None


def build_graph() -> StateGraph:
    g = StateGraph(TradeState)

    g.add_node("fetch_context", node_fetch_context)
    g.add_node("check_safety_gates", node_check_safety_gates)
    g.add_node("handle_blocked", node_handle_blocked)
    g.add_node("call_pi_decision", node_call_pi_decision)
    g.add_node("check_regime_compliance", node_check_regime_compliance)
    g.add_node("process_result", node_process_result)

    g.set_entry_point("fetch_context")

    g.add_edge("fetch_context", "check_safety_gates")
    g.add_conditional_edges(
        "check_safety_gates", _route_after_gates,
        {"handle_blocked": "handle_blocked", "call_pi_decision": "call_pi_decision"},
    )
    g.add_edge("handle_blocked", END)
    g.add_edge("call_pi_decision", "check_regime_compliance")
    g.add_edge("check_regime_compliance", "process_result")
    g.add_edge("process_result", END)

    return g


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph().compile()
    return _graph


def _log_auto_trade_decision(task_id, execution_id, state) -> None:
    try:
        import json as _j, os as _os
        p = _os.path.join(_os.environ.get("DATA_DIR", "data"), "auto_trade_decision_log.jsonl")
        stance, pos, reason, report = state.get("pi_stance"), state.get("pi_position_limit"), state.get("pi_reason"), state.get("report") or ""
        row = {"at": __import__("datetime").datetime.now().isoformat(), "task_id": task_id,
               "execution_id": execution_id, "window": state.get("window"),
               "market_regime": state.get("market_regime"), "style_regime": state.get("style_regime"),
               "pi_stance": stance, "pi_position_limit": pos, "pi_reason": reason,
               "hard_blocked": state.get("hard_blocked"), "regime_violation": state.get("regime_violation"),
               "rotation_switch_ctx": bool(state.get("rotation_switch_context")),
               "report_head": report[:3000], "error": state.get("error") or ""}
        _os.makedirs(_os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(_j.dumps(row, ensure_ascii=False) + chr(10))
    except Exception:
        pass


def run_trade_decision(task_id: str, execution_id: str, pi_prompt: str) -> TradeState:
    """
    运行交易决策图。

    Args:
        task_id: 任务 ID
        execution_id: 本次执行 UUID
        pi_prompt: 任务配置中的 pi_prompt 字段

    Returns:
        TradeState: 含 report / pi_stance / pi_position_limit / pi_reason
    """
    initial: TradeState = {
        "task_id": task_id,
        "execution_id": execution_id,
        "window": _infer_window(task_id, pi_prompt),
        "scan_report_text": "",
        "portfolio_json": "{}",
        "pool_context": "",
        "stance_context": "",
        "trade_mode_instruction": "",
        "regime_context": "",
        "style_context": "",
        "market_regime": "trend",
        "style_regime": "NEUTRAL",
        "drawdown_pct": 0.0,
        "consecutive_losses": 0,
        "hard_blocked": False,
        "block_reason": "",
        "regime_violation": False,
        "regime_violation_reason": "",
        "pi_raw_reply": "",
        "pi_stance": "yellow",
        "pi_position_limit": 60,
        "pi_reason": "",
        "report": "",
        "error": "",
    }
    result = get_graph().invoke(initial)
    _log_auto_trade_decision(task_id, execution_id, result)
    return result
