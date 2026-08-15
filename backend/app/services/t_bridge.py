# -*- coding: utf-8 -*-
"""做T系统 · Worker 主动唤醒 + Agent 决策桥接。

依据 final-t-plan.md §④/§⑥ 与 spec t-monitor-trigger / t-execution-risk / t-ai-agentic：
- Worker 命中后主动 POST bridge /chat 唤醒做T Agent（附触发上下文），Agent 不轮询
- AI 主导模式：AI 是唯一决策主体，唤醒后自主看盘决策（exec/wait/abandon/update_condition）
- 桥不可达降级低频轮询兜底（只标记事件待处理，不自动下单）；Worker 永不直接下单
"""
import json
import time
import urllib.request
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.services import t_db
from app.services.t_gateway import classify_escalation
from app.services.t_regime import compute_regime

# 唤醒降级轮询（桥不可达兜底）
FALLBACK_POLL_INTERVAL = 30.0
# 本地条件单（卖出端秒级）在网关内通过 t_conditions 价位判断承载（见 t_monitor）
# AI 主导模式下连续命中未实质改善的阈值（≥N 次提示 AI 调整/冷却条件）
AI_CONSECUTIVE_HIT_ALERT = 3

_agent_session: Dict[str, str] = {}


def _bridge_url() -> str:
    """bridge /chat 地址（PI_SERVER_URL 已含 /chat 路径段，直接使用，勿重复拼接）。"""
    try:
        settings = get_settings()
        return getattr(settings, "PI_SERVER_URL", "http://127.0.0.1:3001/chat").rstrip("/")
    except Exception:
        return "http://127.0.0.1:3001/chat"


def _position_summary(symbol: str) -> Dict[str, Any]:
    """持仓摘要（唤醒上下文用）：可卖底仓/持仓量/成本/浮动盈亏。"""
    try:
        from app.services.t_gateway import get_sellable_ledger
        ledger = get_sellable_ledger()
        item = ledger.get(symbol) or {}
        return {
            "symbol": symbol,
            "sellable": item.get("sellable", 0),
            "volume": item.get("volume", 0),
            "avg_price": item.get("avg_price"),
            "pnl_pct": item.get("pnl_pct"),
        }
    except Exception as e:
        return {"symbol": symbol, "error": str(e)[:100]}


def _recent_decisions(symbol: str, limit: int = 3) -> List[Dict[str, Any]]:
    """最近 N 次 AI 决策（t_ai_actions 倒序），唤醒上下文供 AI 判断"连续未实质改善"。"""
    try:
        return t_db.list_ai_actions(symbol=symbol, limit=limit)
    except Exception:
        return []


def _consecutive_hits(condition_id: Optional[int], symbol: str) -> int:
    """同条件当日连续命中计数（t_triggers 最近事件，按条件+标的统计）。"""
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            rows = db.execute(text(
                "SELECT status, created_at FROM t_triggers "
                "WHERE condition_id = :cid AND symbol = :sym "
                "AND created_at::date = CURRENT_DATE "
                "ORDER BY id DESC LIMIT 10"
            ), {"cid": condition_id, "sym": symbol}).mappings().all()
            n = 0
            for r in rows:
                # 连续：从最新往前数，遇到 executed/blocked/cancelled 则中断计数
                st = r.get("status")
                if st in ("await_retry", "ai_decided", "pending"):
                    n += 1
                else:
                    break
            return n
        finally:
            db.close()
    except Exception:
        return 0


def wake_agent(trigger: Dict[str, Any], context: Optional[dict] = None) -> Optional[str]:
    """Worker 命中后主动唤醒做T Agent（POST /chat，附触发上下文）。

    AI 主导模式：唤醒语义为"决策"而非"复核"——AI 自主看盘后决定
    exec（执行）/ wait（等待）/ abandon（放弃）/ update_condition（调整条件）。
    返回 AI 回复文本（/chat 响应 reply 字段）；失败返回 None（调用方降级）。
    """
    symbol = trigger.get("symbol", "")
    ctx = dict(context or {})
    # 增强上下文：持仓摘要 + 最近决策 + 连续命中计数（供 AI 判断"连续未实质改善"）
    ctx.setdefault("position", _position_summary(symbol))
    ctx.setdefault("recent_decisions", _recent_decisions(symbol))
    consec = _consecutive_hits(trigger.get("condition_id"), symbol)
    ctx["consecutive_hits"] = consec
    hit_alert = consec >= AI_CONSECUTIVE_HIT_ALERT
    ctx["consecutive_hit_alert"] = hit_alert

    msg = (
        f"【做T触发】{symbol} {trigger.get('event_type', 'low_buy')} "
        f"触发价={trigger.get('trigger_price')} 现价={trigger.get('quote_price')} "
        f"建议买价={trigger.get('suggest_bid_price')} 建议卖价={trigger.get('suggest_ask_price')} "
        f"事件#{trigger.get('id')} 条件#{trigger.get('condition_id')}。"
    )
    if hit_alert:
        msg += (f"⚠️ 该条件已连续命中 {consec} 次且未见实质改善——你必须给出明确的"
                f"调整条件或冷却动作（update_condition），否则该条件将被系统自动冷却。")
    msg += (
        f"你是做T决策者：请检查量价合理性、regime、可卖底仓与最近决策，输出决策 JSON："
        f'{{"action": "exec|wait|abandon|update_condition", "reason": "一句话理由", '
        f'"condition": {{...}}}}（condition 仅在 update_condition 时提供，含 symbol/trigger_kind/target_price 等）。'
        f"exec 将按建议价经网关风控执行；wait/abandon 不成交；update_condition 将更新监控条件。"
        f"如需更多数据可自主调用查询工具（get_stock_quote 实时行情 / get_t_realtime_indicators 技术指标"
        f"/ get_intraday_minute 分钟K线 / get_portfolio_positions 持仓 / get_stock_moneyflow 资金流"
        f"/ get_market_state 大盘），不必只依赖本快照。"
        f"上下文: {json.dumps(ctx, ensure_ascii=False, default=str)[:800]}"
    )
    payload = {
        "message": msg,
        "session_id": _agent_session.setdefault(symbol, f"t-agent-{symbol}"),
        "mode": "trade",
        "decision_mode": "ai_led",
    }
    try:
        req = urllib.request.Request(
            _bridge_url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
        # 解析 AI 回复（reply 字段）
        try:
            reply = str(json.loads(body).get("reply") or "") if body else ""
        except (ValueError, TypeError):
            reply = body or ""
        print(f"[t-bridge] 唤醒 Agent 成功: {symbol} ({len(body)} bytes) decision_mode=ai_led "
              f"reply_len={len(reply)}")
        return reply or None
    except Exception as e:
        print(f"[t-bridge] 唤醒 Agent 失败（降级轮询兜底）: {e}")
        return None


def wake_and_decide(trigger: Dict[str, Any], context: Optional[dict] = None,
                    session_id: Optional[str] = None) -> Dict[str, Any]:
    """AI 主导闭环：唤醒 AI → 取回复 → handle_ai_decision 路由（exec/wait/abandon/update_condition）→ 审计。

    唤醒失败（桥不可达）返回 {"status": "wake_failed"}，由调用方走降级（agent_review_and_execute 标记）。
    """
    reply = wake_agent(trigger, context=context)
    if not reply:
        return {"status": "wake_failed", "reason": "AI 唤醒失败（桥不可达）"}
    from app.services.t_ai_agent import handle_ai_decision
    sid = session_id or f"t-agent-{trigger.get('symbol', '')}"
    return handle_ai_decision(trigger, context, reply, session_id=sid)


def agent_review_and_execute(trigger: Dict[str, Any]) -> Dict[str, Any]:
    """AI 决策降级通道（桥不可达兜底调用）。

    AI 主导模式下此路径仅做合理性标记：异常升级分类命中则置 human_confirm；
    否则标记 ai_decided 等待 AI 下次唤醒处理，绝不自动下单（与 AI 主导语义一致）。
    """
    trigger_id = int(trigger.get("id") or 0)
    symbol = trigger.get("symbol", "")
    side = "buy" if trigger.get("event_type") in ("low_buy", "panic_vibrate") else "sell"
    regime = compute_regime().get("regime", "ACTIVE")

    # 1) 异常升级分类（与 AI 决策共享的硬性升级）
    escalation, why = classify_escalation(symbol, side, trigger=trigger, regime=regime)
    if escalation == "human":
        t_db.update_trigger_status(trigger_id, "human_confirm", reason=why)
        return {"status": "human_confirm", "trigger_id": trigger_id, "reason": why}

    # 2) AI 主导：桥不可达 → 标记待 AI 下次唤醒，不自动下单
    t_db.update_trigger_status(trigger_id, "ai_decided",
                               reason="桥不可达降级：标记待 AI 唤醒决策（不自动下单）")
    return {"status": "ai_decided", "trigger_id": trigger_id,
            "reason": "AI 主导降级：仅标记不自动下单"}


def fallback_poll_loop(stop_event):
    """桥不可达时的低频轮询兜底：消费 pending 事件 → agent_review_and_execute。

    AI 主导模式下兜底仅标记事件（human_confirm / ai_decided），不自动下单。
    """
    while not stop_event.is_set():
        try:
            trig = t_db.claim_pending_trigger("t-fallback", timeout_seconds=300)
            if trig:
                result = agent_review_and_execute(trig)
                print(f"[t-bridge] 兜底处理 #{trig.get('id')}: {result.get('status')}")
        except Exception as e:
            print(f"[t-bridge] 兜底轮询异常: {e}")
        time.sleep(FALLBACK_POLL_INTERVAL)
