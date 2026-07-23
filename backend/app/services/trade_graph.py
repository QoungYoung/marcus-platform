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

import json
import logging
import re
import ssl
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import TypedDict, Optional, Dict, Any

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


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
            acct = db.query(PaperAccountInfo).filter(PaperAccountInfo.id == 1).first()
            initial_cap = float(acct.initial_capital) if acct else 100000.0

            # 当前持仓标的元数据
            pos_rows = db.query(PaperPosition).all()
            held_symbols = {r.symbol: r for r in pos_rows}

            # 全部非撤回成交
            trades = db.query(PaperTrade).filter(
                PaperTrade.volume > 0,
                (PaperTrade.voided == 0) | (PaperTrade.voided == None)
            ).order_by(PaperTrade.created_at).all()

            total_profit = float(
                db.query(func.coalesce(func.sum(PaperTrade.profit), 0)).filter(
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
            if net_vol > 0 and sym in held_symbols:
                avg_cost = pos['buy_amount'] / pos['buy_volume'] if pos['buy_volume'] > 0 else 0
                entry_info = held_symbols[sym]
                positions.append({
                    "symbol": sym,
                    "volume": net_vol,
                    "avg_cost": round(avg_cost, 2),
                    "entry_date": entry_info.entry_date or '',
                    "highest_price": entry_info.highest_price,
                })

        total_buy = sum(t.amount or 0 for t in trades if t.direction == '买入')
        total_sell = sum(t.amount or 0 for t in trades if t.direction == '卖出')
        cash = initial_cap - total_buy + total_sell
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
    """根据风格轮动信号生成策略提示"""
    regime = style_info.get("style_regime", "NEUTRAL")
    days = style_info.get("consecutive_days", 0)
    suggestion = style_info.get("suggestion", "")
    warning = style_info.get("divergence_warning")

    if regime == "OFFENSE":
        block = (
            "\n📊 **风格模式：⚔️ 进攻**\n"
            f"科技风格已连续跑赢 {days} 天（价格+资金双确认）。\n"
            "操作规则：\n"
            "| 规则 | 内容 |\n"
            "|------|------|\n"
            "| 选股偏好 | 优先科技成长类标的（半导体/AI/算力/机器人等） |\n"
            "| 科技仓位上限 | **20%**（从15%上调） |\n"
            "| 防御仓位上限 | **10%**（从15%下调） |\n"
            "| 总仓位上限 | 维持现有配置 |\n"
        )
    elif regime == "DEFENSE":
        block = (
            "\n📊 **风格模式：🛡️ 防御**\n"
            f"防御风格已连续跑赢 {days} 天（价格+资金双确认）。\n"
            "操作规则：\n"
            "| 规则 | 内容 |\n"
            "|------|------|\n"
            "| 科技板块 | **只卖不买**，严禁开新仓 |\n"
            "| 选股偏好 | 积极寻找避险标的（银行/红利/高股息/公用事业） |\n"
            "| 科技仓位上限 | **5%**（从15%下调） |\n"
            "| 防御仓位上限 | **20%**（从15%上调） |\n"
            "| 总仓位上限 | 主力净流出>200亿时降至40% |\n"
        )
    elif regime == "RESOURCE_HEDGE":
        block = (
            "\n📊 **风格模式：🥇 资源避险**\n"
            f"资源风格已连续跑赢 {days} 天（价格+资金双确认）。\n"
            "操作规则：\n"
            "| 规则 | 内容 |\n"
            "|------|------|\n"
            "| 选股偏好 | 关注黄金/有色/能源类标的 |\n"
            "| 资源仓位上限 | **20%**（从15%上调） |\n"
            "| 科技仓位上限 | **10%**（从15%下调） |\n"
        )
    else:
        block = "\n📊 **风格模式：⚖️ 均衡**\n按正常产业链逻辑选股，不做风格偏好。\n"

    if warning:
        block += f"\n{warning}\n"

    if suggestion:
        block += f"\n风格建议: {suggestion}\n"

    return block


def _get_regime_strategy(regime: str) -> str:
    """根据市场结构生成策略切换指令块"""
    if regime == "oscillation":
        return (
            "\n📊 **今日市场结构：🔴 震荡市**\n"
            "你必须严格遵循以下震荡市策略参数，不得使用趋势市策略：\n\n"
            "| 参数 | 🔴 震荡市（当前） | 🟢 趋势市（禁用） |\n"
            "|------|:------:|:------:|\n"
            "| K线周期 | **60分钟**（get_intraday_min freq='60min'） | 日线 |\n"
            "| 均线系统 | **60分MA10 > 60分MA30** | 日线MA5 > MA20 |\n"
            "| 持仓天数 | **1-3天** | 5-30天 |\n"
            "| 单票仓位 | **5-8%** | 10-15% |\n"
            "| 盈亏比目标 | **1:1.5** | 1:3 |\n"
            "| 入场方式 | **回踩通道下沿低吸**，不追突破 | 突破确认后追入 |\n"
            "| 止盈风格 | **到目标即走**，不贪趋势延续 | 分批止盈，趋势不走不止盈 |\n"
            "| 产业链建仓 | **禁用**，不执行产业链计划 | 按产业链上中下游布局 |\n\n"
            "⚠️ 震荡市节奏（关键！）：\n"
            "- 主力在 09:35 就试探，但第一个冒头的往往是诱饵\n"
            "- **09:50 才见真章**，09:50 时的第1名才是真正的龙头\n"
            "- ❌ 禁止使用日线MA5/MA20作为入场信号\n"
            "- ❌ 禁止产业链建仓计划\n"
            "- 🚫 get_intraday_min 是建仓的前置硬门槛：\n"
            "  ① 必须先调用 get_intraday_min(freq='60min') 获取分钟K线\n"
            "  ② 必须确认 60分MA10 > 60分MA30 才能建仓\n"
            "  ③ 如果 get_intraday_min 返回空数据（\"无数据\"）或失败 → ⛔ 本轮禁止建仓！跳过该标的！\n"
            "  ④ 严禁\"调用失败后跳过验证直接下单\"——这是严重违规！\n"
            "  ⑤ 没有例外。分钟数据不可用 = 不建仓。\n"
            "- ✅ 止盈止损紧凑：到达目标盈亏比1:1.5立即执行\n"
        )
    elif regime == "trend":
        return (
            "\n📊 **今日市场结构：🟢 趋势市**\n"
            "严格遵循以下趋势市策略参数：\n\n"
            "| 参数 | 🟢 趋势市（当前） |\n"
            "|------|:------:|\n"
            "| K线周期 | **日线**（get_daily_kline） |\n"
            "| 均线系统 | **MA5 > MA20** |\n"
            "| 持仓天数 | **5-30天** |\n"
            "| 单票仓位 | **10-15%** |\n"
            "| 盈亏比目标 | **1:3** |\n"
            "| 入场方式 | **突破确认后追入** |\n"
            "| 止盈风格 | **分批止盈**，趋势不走不止盈 |\n"
            "| 产业链建仓 | **启用**，按产业链上中下游布局 |\n\n"
            "⚠️ 趋势市节奏（关键！）：\n"
            "- 主力 10:00 前在试探，早盘是噪声，不要动手\n"
            "- **等到 10:35** 日线信号确认后才建仓\n"
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
    """根据时间窗口和市场结构返回交易模式指令。

    趋势市节奏：主力 10:00 前试探，早盘是噪声 → 09:35/09:50 观察不动，10:35 日线确认后建仓 40%
    震荡市节奏：主力 09:35 试探但第一个冒头的是诱饵 → 09:35 记排名不动，09:50 买第1名 60%，10:35 第1名还在则加仓 40%
    """

    if regime == "oscillation":
        return {
            "morning": (
                "现在是早盘 9:35，🔴 震荡市 → **扫描记排名，不买！**\n\n"
                "震荡市第一个冒头的往往是诱饵，9:35 是主力试探窗口，不要建仓。\n\n"
                "你的任务（只做前三步，不买）：\n"
                "1. 调用 get_concept_fund_flow(limit=30, sort_by='main_net') 获取概念板块资金排行\n"
                "2. 调用 get_intraday_min(freq='60min') 扫描资金TOP3概念中60分MA10>MA30的领涨股\n"
                "3. **记录第1名**（涨幅最高+资金净流入为正的那个），记住它的代码和涨幅\n"
                "4. ⛔ 禁止建仓！禁止调用 place_order！只观察和记录！\n\n"
                "输出格式：\n"
                "## Marcus 交易报告 — 震荡市 9:35 观察窗口\n"
                "### 排名记录（供 9:50 确认用）\n"
                "| 排名 | 标的 | 涨幅 | 60分趋势 | 资金 |\n"
                "|:--:|------|-----|:---:|-----|\n"
                "| 🥇 | xxx | +X% | MA10>MA30 ✅ | 净流入X亿 |\n"
                "| 🥈 | xxx | +X% | ⚠️ | ... |\n\n"
                "⛔ 本窗口不建仓。9:50 回来确认第1名是否还在。\n"
                "SIGNAL: yellow POSITION:0 REASON:震荡市9:35观察窗口，记录排名待9:50确认"
            ),
            "mid_morning": (
                "现在是早盘 9:50，🔴 震荡市 → **确认第1名，建仓60%！**\n\n"
                "9:35 记的第1名是诱饵还是真龙？现在确认！\n\n"
                "执行流程：\n"
                "1. 调用 get_concept_fund_flow(limit=30, sort_by='main_net') 获取当前资金排行\n"
                "2. 找到当前的**资金+涨幅双料第1名**\n"
                "3. 判断：\n"
                "   - 第1名没变（和 9:35 一样）→ **买它 60%！** 诱饵已被验证是真龙\n"
                "   - 第1名换了（新面孔冒头）→ **买新第1名 60%！** 谁在 9:50 是第1就买谁\n"
                "4. 调用 get_intraday_min(freq='60min') 确认60分MA10>MA30\n"
                "5. 调用 check_entry_filters + calc_position → place_order\n"
                "6. ⚠️ 单票 ≤ 8%，试探仓 ≤ 5%\n\n"
                "SIGNAL: green POSITION:60 REASON:震荡市9:50确认第1名(SH/SZxxxxxx)，建仓60%"
            ),
            "late_morning": (
                "现在是午前 10:35，🔴 震荡市 → **第1名还在？加仓40%！**\n\n"
                "执行流程：\n"
                "1. 调用 get_concept_fund_flow(limit=30, sort_by='main_net') 获取当前资金排行\n"
                "2. 确认你在 9:50 买入的第1名现在还是不是第1名：\n"
                "   - ✅ 第1名还在 → **加仓 40%！** 总仓位达到 100% 上限\n"
                "   - ❌ 第1名又换了 → **不动！** 已买的不要加仓（说明震荡市无主线，加仓风险大）\n"
                "3. ⚠️ 无论加不加仓，都不要买卖新的标的\n"
                "4. 检查已持仓标的的60分趋势是否仍在（MA10仍>MA30？），趋势跌破→止损/止盈\n"
                "5. 如果 9:50 没有建仓（当时无合格标的）→ 本窗口也不建仓，直接跳过\n\n"
                "SIGNAL: green POSITION:100 REASON:震荡市10:35第1名仍在，加仓40%至满仓"
                if "第1名还在" else
                "SIGNAL: yellow POSITION:60 REASON:震荡市10:35第1名已换，维持60%不加仓"
            ),
            "afternoon": (
                "现在是午后 13:35，🔴 震荡市 → **只卖不买！**\n\n"
                "1. 对持仓逐只检查（跳过今日买入的 T+1 锁定持仓）\n"
                "2. 60分MA10跌破MA30 → 立即止损/止盈，不等待日线确认\n"
                "3. 浮盈已达目标(1:1.5) → 执行止盈，不贪\n"
                "4. ⛔ 禁止新建仓！不寻找新标的！\n\n"
                "SIGNAL: red POSITION:<当前仓位> REASON:午后只卖不买"
            ),
            "closing": (
                "现在是尾盘 14:35，🔴 震荡市 → **只卖不买！**\n\n"
                "严格禁止新开仓。只执行以下操作：\n"
                "⚠️ A股 T+1 规则：今日买入的持仓今日不可卖出，跳过！\n"
                "1. 对持仓逐只检查（跳过今日买入的），止损位触发则立即卖出\n"
                "2. 达到止盈目标的卖出（仅限昨日及之前买入的）\n"
                "3. 60分趋势破位的减仓 50%（排除 T+1 锁定持仓）\n"
                "4. 报告尾盘操作结果，标明哪些持仓因 T+1 锁定未操作\n\n"
                "SIGNAL: red POSITION:<当前仓位> REASON:尾盘只卖不买"
            ),
        }.get(window, "请基于最新扫描报告执行震荡市交易决策。")

    # ── 趋势市 ──
    elif regime == "trend":
        return {
            "morning": (
                "现在是早盘 9:35，🟢 趋势市 → **观察，不动手！**\n\n"
                "趋势市主力 10:00 前在试探，早盘是噪声。不要被开盘脉冲诱骗建仓。\n\n"
                "你的任务（只观察，不买）：\n"
                "1. 调用 get_concept_fund_flow(limit=30, sort_by='main_net') 了解当日热点方向\n"
                "2. 调用 get_market_indices() 看大盘开盘情况\n"
                "3. ⛔ 禁止建仓！禁止调用 place_order！\n\n"
                "输出格式：\n"
                "## Marcus 交易报告 — 趋势市 9:35 观察窗口\n"
                "### 早盘观察\n"
                "- 大盘开盘：{涨跌情况}\n"
                "- 热点方向：{资金TOP3概念}\n"
                "- 当前立场：观察中，等待 10:35 日线确认\n\n"
                "⛔ 本窗口不建仓。趋势市等到 10:35 日线确认后才动手。\n"
                "SIGNAL: yellow POSITION:0 REASON:趋势市早盘噪声期，10:35日线确认后建仓"
            ),
            "mid_morning": (
                "现在是早盘 9:50，🟢 趋势市 → **继续观察，不动手！**\n\n"
                "10:00 前的行情仍处噪声期，主力还在试探方向。不要动手。\n\n"
                "你的任务：\n"
                "1. 回顾 9:35 观察的热点方向是否仍在（资金排名没有大换血？）\n"
                "2. 调用 get_daily_kline 查看候选标的日线形态（MA5>MA20？突破前高？）\n"
                "3. 提前完成产业链建仓计划表（不买，只规划）\n"
                "4. ⛔ 禁止建仓！禁止调用 place_order！\n\n"
                "SIGNAL: yellow POSITION:0 REASON:趋势市继续观察，等待10:35"
            ),
            "late_morning": (
                "现在是午前 10:35，🟢 趋势市 → **日线信号确认，建仓40%！**\n\n"
                "10:00 已过，噪声消退，日线方向明确。现在可以动手！\n\n"
                "执行流程：\n"
                "1. 调用 get_concept_fund_flow(limit=30, sort_by='main_net') 确认当日主线\n"
                "2. 完成产业链建仓计划表（上中下游各 1 只）→ 买入上游龙头\n"
                "3. 每只买入前必须调用 check_entry_filters + calc_position\n"
                "4. ⚠️ 总仓位目标 40%，单票 10-15%\n"
                "5. 止损设在日线 MA5 下方\n\n"
                "SIGNAL: green POSITION:40 REASON:趋势市10:35日线确认，建仓40%"
            ),
            "afternoon": (
                "现在是午后 13:35，🟢 趋势市 → **只卖不买！**\n\n"
                "1. 对持仓逐只检查（跳过今日买入的 T+1 锁定持仓）\n"
                "2. 趋势破位（日线跌破 MA5 或 MACD 死叉）→ 减仓 50%\n"
                "3. 盈利 10%+ → 卖 1/3 分批止盈\n"
                "4. ⛔ 禁止新建仓！\n\n"
                "SIGNAL: red POSITION:<当前仓位> REASON:午后只卖不买"
            ),
            "closing": (
                "现在是尾盘 14:35，🟢 趋势市 → **只卖不买！**\n\n"
                "严格禁止新开仓。只执行以下操作：\n"
                "⚠️ A股 T+1 规则：今日买入的持仓今日不可卖出，跳过！\n"
                "1. 对持仓逐只检查（跳过今日买入的），止损位触发则立即卖出\n"
                "2. 达到止盈目标的卖出（仅限昨日及之前买入的）\n"
                "3. 趋势破位的减仓 50%（排除 T+1 锁定持仓）\n"
                "4. 报告尾盘操作结果，标明哪些持仓因 T+1 锁定未操作\n\n"
                "SIGNAL: red POSITION:<当前仓位> REASON:尾盘只卖不买"
            ),
        }.get(window, "请基于最新扫描报告执行趋势市交易决策。")

    # ── 未知市场结构 ──
    return {
        "morning": (
            "⛔ 今日市场结构**未诊断**（盘前诊断 morning_diagnosis 尚未执行或执行失败）。\n\n"
            "严格禁止按任何默认策略操作！当前只能执行安全操作：\n"
            "1. 调用 get_positions() 查看当前持仓和盈亏状态\n"
            "2. 检查是否有触发止损/止盈的持仓需要处理\n"
            "3. ⛔ 禁止建新仓！禁止加仓！\n"
            "4. 报告当前持仓状态，等待下一次交易窗口\n\n"
            "SIGNAL: red POSITION:0 REASON:市场结构未诊断，禁止建仓"
        ),
        "mid_morning": (
            "⛔ 今日市场结构**未诊断**。\n\n"
            "如果盘前诊断仍未执行，继续禁止建仓。检查持仓是否需要止损/止盈处理。\n"
            "如果盘前诊断已完成，请以诊断结果为准（趋势市或震荡市）。\n\n"
            "SIGNAL: red POSITION:0 REASON:市场结构未诊断，禁止建仓"
        ),
        "late_morning": (
            "⛔ 今日市场结构**未诊断**。\n\n"
            "10:35 已过，如果诊断仍未完成，今天不建议建仓。\n"
            "仅执行止损/止盈检查。\n\n"
            "SIGNAL: red POSITION:0 REASON:市场结构未诊断，今日不建议建仓"
        ),
        "afternoon": (
            "⛔ 今日市场结构**未诊断** → **只检查止损，不建仓！**\n\n"
            "1. 对持仓逐只检查（跳过今日买入的 T+1 锁定持仓）\n"
            "2. 止损位触发则立即卖出\n"
            "3. ⛔ 禁止新建仓！\n\n"
            "SIGNAL: red POSITION:<当前仓位> REASON:午后只卖不买"
        ),
        "closing": (
            "⛔ 今日市场结构**未诊断** → **只检查止损，不建仓！**\n\n"
            "严格禁止新开仓。只执行以下操作：\n"
            "⚠️ A股 T+1 规则：今日买入的持仓今日不可卖出，跳过！\n"
            "1. 对持仓逐只检查（跳过今日买入的），止损位触发则立即卖出\n"
            "2. 报告尾盘操作结果\n\n"
            "SIGNAL: red POSITION:<当前仓位> REASON:尾盘只卖不买"
        ),
    }.get(window, "市场结构未诊断，禁止交易操作。请等待盘前诊断完成。")


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


def _call_pi(prompt: str, task_id: str, timeout: int = 600) -> str:
    """调用 Pi Server /chat 端点"""
    pi_url = _get_pi_server_url()
    payload = json.dumps({
        "message": prompt,
        "session_id": f"pi_trade_{task_id}_{datetime.now().strftime('%Y%m%d')}",
        "mode": "trade",
    }).encode("utf-8")

    req = urllib.request.Request(
        pi_url, data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("reply", "")
    return ""


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
        "regime_context": _get_regime_strategy(regime),
        "style_context": _get_style_strategy(style_info),
        "market_regime": regime,
        "style_regime": style_info.get("style_regime", "NEUTRAL"),
        "trade_mode_instruction": _get_trade_instruction(state['window'], regime),
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

    drawdown, blocked, reason = _check_drawdown(state['portfolio_json'])
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

    prompt = (
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
        reply = _call_pi(prompt, state['task_id'])
    except Exception as e:
        logger.error(f"[{eid}] [Graph] Pi 调用异常: {e}")
        return {"error": f"Pi Server 调用失败: {e}"}

    if not reply or reply == '(无回复)':
        return {"error": "Pi 未返回有效交易报告"}

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

    if regime != 'oscillation':
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
    return result
