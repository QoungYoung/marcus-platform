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
import sqlite3
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

    # 安全门
    drawdown_pct: float
    consecutive_losses: int
    hard_blocked: bool
    block_reason: str

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
    """读取账户持仓数据（直接读 trades.db 计算，避免进程内 HTTP 调用）

    实际 DB schema:
      positions: symbol, entry_date, highest_price, updated_at
      trades: id, orderid, symbol, direction, price, volume, amount, profit, created_at, trade_date
    """
    try:
        workspace = _get_workspace()
        db_path = workspace / "data" / "trades.db"
        if not db_path.exists():
            return "{}"

        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # 当前持仓标的
        pos_rows = conn.execute(
            "SELECT symbol, entry_date, highest_price, updated_at FROM positions"
        ).fetchall()
        held_symbols = {r['symbol']: r for r in pos_rows}

        # 全部成交记录
        trades = conn.execute(
            "SELECT symbol, direction, price, volume, amount, profit, trade_date "
            "FROM trades WHERE volume > 0 ORDER BY created_at"
        ).fetchall()

        # 按标的汇总持仓量和成本
        symbol_pos: dict = {}
        for t in trades:
            sym = t['symbol']
            if sym not in symbol_pos:
                symbol_pos[sym] = {'buy_volume': 0, 'buy_amount': 0.0, 'sell_volume': 0, 'sell_amount': 0.0}
            if t['direction'] == 'buy':
                symbol_pos[sym]['buy_volume'] += (t['volume'] or 0)
                symbol_pos[sym]['buy_amount'] += (t['amount'] or 0)
            elif t['direction'] == 'sell':
                symbol_pos[sym]['sell_volume'] += (t['volume'] or 0)
                symbol_pos[sym]['sell_amount'] += (t['amount'] or 0)

        # 当前持仓明细
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
                    "entry_date": entry_info['entry_date'] or '',
                    "highest_price": entry_info['highest_price'],
                })

        # 账户汇总
        total_buy = sum(t['amount'] or 0 for t in trades if t['direction'] == 'buy')
        total_sell = sum(t['amount'] or 0 for t in trades if t['direction'] == 'sell')
        total_profit = sum(t['profit'] or 0 for t in trades)
        cash = 100000.0 - total_buy + total_sell  # 初始资金 10 万
        total_cost = sum(p['avg_cost'] * p['volume'] for p in positions)
        total_asset = cash + total_cost  # 简化：持仓市值 ≈ 成本（无实时价时用成本近似）

        # 今日买入（T+1 锁定）
        today = datetime.now().strftime('%Y-%m-%d')
        today_bought = list(set(
            t['symbol'] for t in trades
            if t['direction'] == 'buy' and (t.get('trade_date') or '') == today
        ))

        conn.close()

        return json.dumps({
            "initial_capital": 100000,
            "cash": round(cash, 2),
            "total_cost": round(total_cost, 2),
            "total_asset": round(total_asset, 2),
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


def _get_trade_instruction(window: str) -> str:
    """根据时间窗口返回交易模式指令"""
    return {
        "morning": (
            "现在是早盘 9:35，进入**产业链建仓计划+上游龙头建仓模式**。\n"
            "1. 先完成产业链建仓计划表（规划全部3个环节），再买入上游龙头\n"
            "2. 重点检查上游标的盘中实时MA（get_realtime_indicators）和日内分位\n"
            "3. 严格按照右侧交易 SOP 建仓"
        ),
        "mid_morning": (
            "现在是早盘 9:53，进入**产业链中游跟进建仓模式**。\n"
            "1. 检查上游持仓走势，确认站住分时均线\n"
            "2. 买入建仓计划表中的中游标的（如有）\n"
            "3. 若中游无合格标的，跳过该环节并记录原因\n"
            "4. 检查 get_portfolio 中上游持仓是否已被 PositionTierMonitor 自动加仓"
        ),
        "late_morning": (
            "现在是午前 10:35，进入**产业链收尾+趋势确认模式**。\n"
            "1. 评估已建仓标的走势，不符合预期的及时止损\n"
            "2. 如早盘尚未完成全部产业链覆盖，在此窗口完成下游建仓\n"
            "3. 扫描报告中新出现的强势标的，可按右侧交易 SOP 新建仓"
        ),
        "afternoon": (
            "现在是午后 13:35，进入**午后修正模式**。\n"
            "⚠️ T+1 隔夜风险：下午建仓无法当日退出，需比早盘更严格。\n"
            "1. 新建仓条件：涨幅 ≤ 3% 且 日内分位 ≤ 60%\n"
            "2. 不满足建仓条件 → 只做持仓管理（止损/止盈/减仓），不强行寻找替代标的\n"
            "3. check_entry_filters 返回 hard_block=true → 无条件放弃该标的"
        ),
        "closing": (
            "现在是尾盘 14:30，进入**closing 模式**。\n"
            "**严格禁止新开仓**。只执行以下操作：\n"
            "⚠️ A股 T+1 规则：今日买入的持仓今日不可卖出，跳过！\n"
            "1. 对持仓逐只检查（跳过今日买入的），止损位触发则立即卖出\n"
            "2. 达到止盈目标的卖出（仅限昨日及之前买入的）\n"
            "3. 趋势破位的减仓 50%（排除 T+1 锁定持仓）\n"
            "4. 报告尾盘操作结果，标明哪些持仓因 T+1 锁定未操作"
        ),
    }.get(window, "请基于最新扫描报告执行自主交易决策。")


def _check_drawdown(portfolio_json: str) -> tuple:
    """检查总回撤，返回 (pct, blocked, reason)"""
    try:
        p = json.loads(portfolio_json)
        total_asset = p.get('total_asset', 100000)
        total_cost = p.get('total_cost', total_asset)
        if total_cost > 0:
            drawdown = (total_asset - total_cost) / total_cost
            if drawdown <= -0.05:
                return drawdown * 100, True, f"总回撤 {drawdown*100:.1f}% 已达 5% 硬止损线"
            return drawdown * 100, False, ""
    except Exception:
        pass
    return 0.0, False, ""


def _check_consecutive_losses() -> int:
    """从 trades.db 查询连续亏损笔数（最近卖出交易的 profit 字段）"""
    try:
        workspace = _get_workspace()
        db_path = workspace / "data" / "trades.db"
        if not db_path.exists():
            return 0
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT profit FROM trades WHERE direction='sell' AND volume > 0 "
            "ORDER BY created_at DESC LIMIT 10"
        ).fetchall()
        conn.close()
        count = 0
        for r in rows:
            if r['profit'] is not None and r['profit'] < 0:
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
    """仓位利用率检测：Pi 建议仓位 vs 实际持仓成本占比，脱节时告警"""
    try:
        portfolio_str = _read_portfolio()
        p = json.loads(portfolio_str)
        total_asset = p.get('total_asset', 100000)
        total_cost = p.get('total_cost', 0)  # 用成本作为持仓市值的近似
        actual_pct = (total_cost / total_asset * 100) if total_asset > 0 else 0

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

    直接读取扫描报告、持仓、候选池、上一轮立场、时段指令。
    这些数据之前由 Pi Agent 通过 tool-calling 获取，现在由代码层保证。
    """
    eid = state['execution_id']
    logger.info(f"[{eid}] [Graph] ▶ fetch_context")

    return {
        "scan_report_text": _read_scan_report(),
        "portfolio_json": _read_portfolio(),
        "pool_context": _read_pool_context(state['task_id']),
        "stance_context": _read_stance_context(),
        "trade_mode_instruction": _get_trade_instruction(state['window']),
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
        f"{state['pool_context']}"
        f"{state['trade_mode_instruction']}\n"
        f"{state['stance_context']}"
        f"\n━━━ 系统已预获取的数据（无需重复调用工具）━━━\n\n"
        f"## 最新扫描报告\n```json\n{scan}\n```\n\n"
        f"## 当前账户持仓\n```json\n{state['portfolio_json']}\n```\n\n"
        f"请立即执行以下操作：\n"
        f"1. 分析上方已提供的扫描报告和持仓数据\n"
        f"2. 按右侧交易 SOP 选股分析"
        f"（可调用 check_entry_filters / calc_position / get_quote / "
        f"get_concept_fund_flow / get_realtime_indicators / get_technical 等）\n"
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
    g.add_node("process_result", node_process_result)

    g.set_entry_point("fetch_context")

    g.add_edge("fetch_context", "check_safety_gates")
    g.add_conditional_edges(
        "check_safety_gates", _route_after_gates,
        {"handle_blocked": "handle_blocked", "call_pi_decision": "call_pi_decision"},
    )
    g.add_edge("handle_blocked", END)
    g.add_edge("call_pi_decision", "process_result")
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
        "drawdown_pct": 0.0,
        "consecutive_losses": 0,
        "hard_blocked": False,
        "block_reason": "",
        "pi_raw_reply": "",
        "pi_stance": "yellow",
        "pi_position_limit": 60,
        "pi_reason": "",
        "report": "",
        "error": "",
    }
    result = get_graph().invoke(initial)
    return result
