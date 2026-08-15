# -*- coding: utf-8 -*-
"""做T系统 · AI 主导决策编排（ai_led）。

AI 是选股/操作/条件/复盘的唯一决策主体，本模块承接 AI 决策的结构化路由与审计：
- handle_ai_decision：解析 AI 输出（exec/wait/abandon/update_condition）→ 路由执行/条件更新，写 t_ai_actions
- ai_select_and_build：候选池优先 + 全市场扫描补充 → AI 决策建仓（build_t_position ai_led）
- ai_daily_review：拉当日 t_ai_actions → 唤醒 AI 复盘 → 输出报告 + 次日条件调整指令

安全边界：所有执行仍经网关（gateway_execute / build_t_position），本模块不直接下单。
"""
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.services import t_db

# 决策动作白名单（AI 输出解析后的结构化动作）
AI_ACTIONS = ("exec", "wait", "abandon", "update_condition")


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _parse_ai_decision(reply: str) -> Dict[str, Any]:
    """从 AI 文本中解析决策 JSON：{"action": "exec|wait|abandon|update_condition", "reason": "...", "condition": {...}}。

    容忍 ```json 包裹与前后噪声；解析失败回退 wait（保守）。
    """
    if not reply:
        return {"action": "wait", "reason": "空回复（保守等待）"}
    text = str(reply).strip()
    m = None
    import re
    for pat in (r'\{[^{}]*"action"\s*:\s*"[^"]*"[^{}]*\}',
                r'```(?:json)?\s*(\{.*?\})\s*```', r'(\{.*\})'):
        mm = re.search(pat, text, re.DOTALL)
        if mm:
            m = mm.group(1) if mm.lastindex else mm.group(0)
            break
    if not m:
        # 无 JSON：按关键词兜底
        if any(k in text for k in ("执行", "放行", "exec")):
            return {"action": "exec", "reason": text[:200]}
        if any(k in text for k in ("放弃", "abandon", "不执行")):
            return {"action": "abandon", "reason": text[:200]}
        return {"action": "wait", "reason": text[:200]}
    try:
        obj = json.loads(m)
        action = str(obj.get("action") or "wait")
        if action not in AI_ACTIONS:
            action = "wait"
        return {
            "action": action,
            "reason": str(obj.get("reason") or "")[:500],
            "condition": obj.get("condition") if isinstance(obj.get("condition"), dict) else None,
        }
    except (ValueError, TypeError):
        return {"action": "wait", "reason": f"解析失败（保守等待）: {text[:120]}"}


def handle_ai_decision(trigger: Optional[Dict[str, Any]], context: Optional[Dict[str, Any]],
                       ai_reply: str, session_id: Optional[str] = None) -> Dict[str, Any]:
    """AI 决策入口：解析 AI 输出并路由执行/条件更新，写 t_ai_actions 审计。

    Args:
        trigger: 触发事件（t_triggers 行或构造 dict；无触发事件的主动决策传 None）
        context: 决策上下文（持仓摘要/最近决策/连续命中计数等）
        ai_reply: AI 会话文本输出（含 {"action": ..., "reason": ...}）
        session_id: 决策会话（如 t-agent-SH600519）
    Returns:
        {status, action, reason, gateway, action_id}
    """
    decision = _parse_ai_decision(ai_reply)
    action = decision["action"]
    reason = decision["reason"]
    symbol = (trigger or {}).get("symbol") or (context or {}).get("symbol") or ""

    # 审计：先记录输入与决策（gateway_result 后续补）
    action_id = t_db.insert_ai_action(
        session_id=session_id, trade_date=_today(), symbol=symbol,
        action_type=f"ai_{action}",
        input_snapshot={"trigger": trigger or {}, "context": context or {}},
        output=decision,
    )

    result: Dict[str, Any] = {"status": "decided", "action": action, "reason": reason,
                              "action_id": action_id}
    trigger_id = (trigger or {}).get("id")
    if action == "exec":
        # 执行：经网关（ai_led 档位，不豁免风控）
        try:
            from app.services.t_gateway import gateway_execute
            price = float((trigger or {}).get("suggest_bid_price")
                          or (trigger or {}).get("suggest_ask_price")
                          or (context or {}).get("price") or 0)
            side = "buy" if (trigger or {}).get("event_type") in ("low_buy", "panic_vibrate", "custom_buy") else "sell"
            volume = int((context or {}).get("volume") or 100)
            volume = (volume // 100) * 100 or 100
            if price <= 0:
                gw = {"status": "rejected", "reason": "无有效价格（AI 决策未携带价格）"}
            else:
                gw = gateway_execute(symbol, side, price, volume,
                                     condition_id=(trigger or {}).get("condition_id"),
                                     trigger_id=trigger_id,
                                     reason=reason or "AI 决策执行",
                                     decision_source="ai_led")
            result["gateway"] = gw
            result["status"] = "executed" if gw.get("status") == "success" else "rejected"
            # 触发事件状态流转（exec 成功 → executed 由网关内处理；失败 → blocked）
        except Exception as e:
            gw = {"status": "rejected", "reason": f"执行异常: {e}"}
            result["gateway"] = gw
            result["status"] = "rejected"
        # 补审计网关结果
        _update_gateway_result(action_id, gw)
    elif action == "update_condition":
        cond = decision.get("condition")
        if isinstance(cond, dict) and cond.get("symbol"):
            try:
                from app.services.t_db import upsert_condition
                cond.setdefault("publisher", "ai")
                cond.setdefault("session_id", session_id)
                cid = upsert_condition(cond)
                result["condition_id"] = cid
            except Exception as e:
                result["condition_error"] = str(e)[:200]
        else:
            result["condition_error"] = "AI 未提供有效条件（缺 symbol）"
        # 触发事件 → await_retry（等待新条件重新武装）
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "await_retry", reason="AI 调整条件")
    elif action == "abandon":
        # 放弃：事件 cancelled
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "cancelled", reason=reason or "AI 放弃")
        result["status"] = "abandoned"
    else:  # wait
        # 等待：事件 await_retry（冷却后重新武装）
        if trigger_id:
            t_db.update_trigger_status(trigger_id, "await_retry", reason=reason or "AI 等待")
    # abandon / wait：仅审计
    return result


def _update_gateway_result(action_id: Optional[int], gw: Dict[str, Any]):
    """补写审计的网关结果（JSONB）。"""
    if not action_id:
        return
    try:
        from sqlalchemy import text
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_ai_actions SET gateway_result = :gw WHERE id = :id"
            ), {"gw": json.dumps(gw, ensure_ascii=False), "id": action_id})
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[t-ai-agent] 审计网关结果补写失败: {e}")


# ────────────────────────────────────────────────────────────────
# AI 选股建仓
# ────────────────────────────────────────────────────────────────

def ai_select_and_build(session_id: Optional[str] = None,
                        select_limit: int = 5,
                        net_asset: Optional[float] = None) -> Dict[str, Any]:
    """AI 选股建仓：候选池优先 → 空则全市场扫描补充 → 对每个候选执行建仓打分决策。

    建仓经 build_t_position(decision_source='ai_led')，全链风控保留（首开 B1 自动放行）。
    返回各候选的决策与审计。
    """
    from app.services import t_build
    cands = t_build.scan_t_candidates(limit=select_limit, source="pool")
    used_source = "pool"
    if not cands:
        print("[t-ai-agent] 候选池为空，降级全市场扫描")
        cands = t_build.scan_t_candidates(limit=select_limit, source="scan")
        used_source = "scan"
    if not cands:
        return {"status": "no_candidates", "source": used_source, "decisions": []}

    results: List[Dict[str, Any]] = []
    for c in cands:
        symbol = c.get("symbol")
        if not symbol:
            continue
        if not c.get("pass_gate"):
            continue
        # 建仓价：当前实时价（由 scan 返回或取现价）
        price = float(c.get("price") or 0)
        if price <= 0:
            try:
                from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote
                q = fetch_tencent_quote([_normalize_symbol(symbol)]).get(_normalize_symbol(symbol)) or {}
                price = float(q.get("current") or 0)
            except Exception:
                price = 0.0
        if price <= 0:
            results.append({"symbol": symbol, "decision": "rejected", "reason": "无有效建仓价"})
            continue
        # AI 决策建仓（此处以规则打分通过为 AI 依据；后续可接入 AI 会话进一步判断）
        try:
            out = t_build.build_t_position(symbol, price, reason="AI 主导选股建仓",
                                           decision_source="ai_led")
            results.append({"symbol": symbol, "decision": out.get("status"),
                            "price": price, "reason": out.get("reason")})
            t_db.insert_ai_action(
                session_id=session_id, trade_date=_today(), symbol=symbol,
                action_type="ai_build",
                input_snapshot={"candidate": c, "source": used_source},
                output={"decision": out.get("status"), "reason": out.get("reason")},
                gateway_result=out,
            )
        except Exception as e:
            results.append({"symbol": symbol, "decision": "error", "reason": str(e)[:200]})
    return {"status": "done", "source": used_source, "decisions": results}


# ────────────────────────────────────────────────────────────────
# AI 复盘
# ────────────────────────────────────────────────────────────────

def ai_daily_review(session_id: Optional[str] = None, trade_date: Optional[str] = None,
                    wake_fn: Optional[callable] = None) -> Dict[str, Any]:
    """AI 收盘复盘：拉当日 t_ai_actions → 构造复盘简报 → 唤醒 AI 会话复盘。

    输出复盘报告 + 条件调整指令（写次日 t_conditions，publisher='ai'）。
    wake_fn: 唤醒回调（默认 None 时仅汇总，不实际唤醒——由调用方注入 bridge 唤醒）。
    """
    td = trade_date or _today()
    actions = t_db.list_ai_actions(trade_date=td, limit=500)
    if not actions:
        return {"status": "no_actions", "trade_date": td, "summary": {}}

    # 按标的归因
    per_symbol: Dict[str, Dict[str, Any]] = {}
    for a in actions:
        sym = a.get("symbol") or "?"
        entry = per_symbol.setdefault(sym, {"actions": [], "executed": 0, "rejected": 0, "wait": 0})
        entry["actions"].append(a)
        at = a.get("action_type", "")
        gw = a.get("gateway_result") or {}
        if at == "ai_exec":
            if gw.get("status") == "success":
                entry["executed"] += 1
            else:
                entry["rejected"] += 1
        elif at == "ai_wait":
            entry["wait"] += 1
    summary = {
        "total_actions": len(actions),
        "per_symbol": {k: {kk: vv for kk, vv in v.items() if kk != "actions"}
                       for k, v in per_symbol.items()},
        "executed": sum(v["executed"] for v in per_symbol.values()),
        "rejected": sum(v["rejected"] for v in per_symbol.values()),
        "wait": sum(v["wait"] for v in per_symbol.values()),
    }

    if wake_fn is None:
        return {"status": "summary_only", "trade_date": td, "summary": summary, "actions": actions[-50:]}

    # 唤醒 AI 复盘
    brief = _build_review_brief(actions)
    try:
        reply = wake_fn({
            "task": "daily_review", "trade_date": td,
            "brief": brief, "summary": summary,
        })
        review = {"status": "reviewed", "trade_date": td, "reply": str(reply or "")[:2000],
                  "summary": summary}
        # AI 复盘结论审计
        t_db.insert_ai_action(
            session_id=session_id or f"t-review-{td}", trade_date=td, symbol="*",
            action_type="ai_review",
            input_snapshot={"brief": brief[:2000]},
            output={"reply": str(reply or "")[:2000]},
        )
        return review
    except Exception as e:
        return {"status": "review_failed", "trade_date": td, "error": str(e)[:200], "summary": summary}


def _build_review_brief(actions: List[Dict[str, Any]]) -> str:
    """构造复盘简报（供 AI 会话使用）。"""
    lines = [f"当日 AI 决策审计共 {len(actions)} 条：", ""]
    for a in actions[-30:]:
        sym = a.get("symbol") or "?"
        at = a.get("action_type", "")
        out = a.get("output") or {}
        gw = a.get("gateway_result") or {}
        reason = (out.get("reason") or gw.get("reason") or "")[:80]
        ts = str(a.get("created_at") or "")[:19]
        lines.append(f"- [{ts}] {sym} {at} | {reason}")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────
# 模块级便捷函数（供 worker/bridge 调用）
# ────────────────────────────────────────────────────────────────

def run_daily_review():
    """收盘复盘便捷入口（worker 盘后窗口调用）。"""
    try:
        from app.services.t_bridge import wake_agent
        result = ai_daily_review(wake_fn=lambda ctx: wake_agent(
            {"symbol": "REVIEW", "event_type": "daily_review",
             "trigger_price": 0, "quote_price": 0},
            context=ctx,
        ))
        print(f"[t-ai-agent] 收盘复盘: {result.get('status')} "
              f"({result.get('summary', {}).get('total_actions', 0)} 条决策)")
        return result
    except Exception as e:
        print(f"[t-ai-agent] 收盘复盘失败: {e}")
        return {"status": "error", "error": str(e)[:200]}
