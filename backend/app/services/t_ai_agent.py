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


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """提取首个平衡 JSON 对象（支持嵌套 condition 对象，对齐 bridge parseDecision）。

    迭代#54 P8：旧正则 {[^{}]*} 无法匹配嵌套 condition → update_condition 实盘解析
    必失败落 rule_fallback，条件更新实盘从未生效。
    """
    t = str(text).replace("```json", "").replace("```", "")
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except (ValueError, TypeError):
                    return None
    return None


def _parse_ai_decision(reply: str) -> Dict[str, Any]:
    """从 AI 文本中解析决策 JSON：{"action": "exec|wait|abandon|update_condition", "reason": "...", "condition": {...}}。

    容忍 ```json 包裹与前后噪声；解析失败返回 action=rule_fallback（由 handle_ai_decision
    按规则评审生成默认动作，reason 标注 [rule_fallback]——不再一律 wait 保守等待，
    避免高抛兑现/合理买点被解析失败卡死，P3-2）。
    """
    if not reply:
        return {"action": "rule_fallback", "reason": "空回复（规则兜底）"}
    text = str(reply).strip()
    obj = _extract_json_object(text)
    if obj is not None:
        action = str(obj.get("action") or "rule_fallback")
        if action not in AI_ACTIONS:
            action = "rule_fallback"
        return {
            "action": action,
            "reason": str(obj.get("reason") or "")[:500],
            "condition": obj.get("condition") if isinstance(obj.get("condition"), dict) else None,
        }
    # 无 JSON：按关键词兜底
    if any(k in text for k in ("执行", "放行", "exec")):
        return {"action": "exec", "reason": text[:200]}
    if any(k in text for k in ("放弃", "abandon", "不执行")):
        return {"action": "abandon", "reason": text[:200]}
    return {"action": "rule_fallback", "reason": f"无 JSON 无法解析（规则兜底）: {text[:200]}"}


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
    # 规则兜底（P3-2）：解析失败/异常时按触发方向给规则默认动作——
    # 高抛卖腿（兑现离场）在非 BLOCKED regime 默认 exec；低吸买腿默认 wait（保守不追）；
    # reason 标注 [rule_fallback]，审计可归因。
    if action == "rule_fallback":
        ev_type = (trigger or {}).get("event_type") or ""
        regime = (context or {}).get("regime") or "ACTIVE"
        if ev_type in ("high_sell_then_buy_back", "high_sell"):
            if regime == "BLOCKED":
                action, reason = "wait", "[rule_fallback] 高抛兜底 wait（regime 禁卖）"
            else:
                action, reason = "exec", "[rule_fallback] 高抛兜底 exec（兑现离场）"
        else:
            if regime == "HALT":
                action, reason = "abandon", "[rule_fallback] 低吸兜底 abandon（regime=HALT）"
            else:
                action, reason = "wait", "[rule_fallback] 低吸兜底 wait（保守等待）"
        result["action"] = action
        result["reason"] = reason
        result["fallback"] = True
    if action == "exec":
        # 执行：经网关（ai_led 档位，不豁免风控）
        try:
            from app.services.t_gateway import gateway_execute
            price = float((trigger or {}).get("suggest_bid_price")
                          or (trigger or {}).get("suggest_ask_price")
                          or (context or {}).get("price") or 0)
            side = "buy" if (trigger or {}).get("event_type") in ("low_buy", "panic_vibrate", "custom_buy") else "sell"
            # 量：优先 context.volume；否则按可卖底仓 30% 推导（对齐做T单笔惯例，最小 100 股）
            volume = int((context or {}).get("volume") or 0)
            if volume <= 0:
                try:
                    from app.services.t_gateway import get_sellable_ledger
                    item = get_sellable_ledger().get(symbol) or {}
                    sellable = int(item.get("sellable") or 0)
                    volume = max(int(sellable * 0.3), 100) if sellable > 0 else 100
                except Exception:
                    volume = 100
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
    # 先回填当日 AI exec 成交的 outcome（收盘统一评估后续行情）
    try:
        record_outcome(trade_date=td)
    except Exception as e:
        print(f"[t-ai-agent] 收盘 outcome 回填失败: {e}")
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
# 决策质量统计（exec 胜率 / abandon 正确率 / wait 转化）
# ────────────────────────────────────────────────────────────────

def _direction_normalized(oc: Dict[str, Any], side: Optional[str] = None) -> Optional[float]:
    """方向归一：低吸/买（side='buy'）看后续涨为正、高抛/卖看后续跌为正。

    返回归一后的 pct_change（>0 = 决策正确方向）；无法判断返回 None。
    """
    try:
        pct = float(oc.get("pct_change") or 0)
    except (TypeError, ValueError):
        return None
    side = side or oc.get("side")
    if side == "sell":
        return -pct  # 高抛：后续跌为正
    return pct      # 低吸/买/未知：后续涨为正


def decision_quality(symbol: Optional[str] = None, trade_date: Optional[str] = None,
                     actions: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """AI 决策质量指标：exec 胜率、abandon 正确率、wait 转化、分布与平均盈亏。

    基于已回填 outcome 的记录统计（方向归一：低吸买涨/高抛卖跌）。
    """
    if actions is None:
        actions = t_db.list_ai_actions(trade_date=trade_date or None,
                                       symbol=symbol or None, limit=1000)
    stats: Dict[str, Any] = {
        "total": len(actions),
        "exec": {"count": 0, "win": 0, "loss": 0, "avg_pct": 0.0},
        "wait": {"count": 0, "to_exec": 0},
        "abandon": {"count": 0, "correct": 0, "wrong": 0},
        "update_condition": 0, "build": 0,
        "exec_win_rate_pct": None, "abandon_correct_rate_pct": None,
        "wait_to_exec_rate_pct": None,
    }
    exec_pcts: List[float] = []
    # wait 后同标的后续是否转 exec（简化：wait 记录 + 该标的后续存在 exec）
    wait_syms: Dict[str, bool] = {}
    exec_syms: set = set()

    for a in actions:
        at = a.get("action_type", "")
        sym = a.get("symbol") or ""
        oc = a.get("outcome") or {}
        if at == "ai_exec":
            stats["exec"]["count"] += 1
            exec_syms.add(sym)
            norm = _direction_normalized(oc)
            if norm is None:
                continue
            exec_pcts.append(norm)
            if norm > 0:
                stats["exec"]["win"] += 1
            else:
                stats["exec"]["loss"] += 1
        elif at == "ai_wait":
            stats["wait"]["count"] += 1
            wait_syms[sym] = True
        elif at == "ai_abandon":
            stats["abandon"]["count"] += 1
            norm = _direction_normalized(oc)
            if norm is None:
                continue
            # 放弃正确 = 后续走向对"不做"有利（低吸放弃后继续跌 = 正确）
            if norm < 0:
                stats["abandon"]["correct"] += 1
            else:
                stats["abandon"]["wrong"] += 1
        elif at == "ai_update_condition":
            stats["update_condition"] += 1
        elif at == "ai_build":
            stats["build"] += 1

    if exec_pcts:
        stats["exec"]["avg_pct"] = round(sum(exec_pcts) / len(exec_pcts), 3)
        stats["exec_win_rate_pct"] = round(
            stats["exec"]["win"] / stats["exec"]["count"] * 100, 2) if stats["exec"]["count"] else None
    ab_total = stats["abandon"]["correct"] + stats["abandon"]["wrong"]
    if ab_total:
        stats["abandon_correct_rate_pct"] = round(
            stats["abandon"]["correct"] / ab_total * 100, 2)
    # wait 转化：wait 出现的标的后续有 exec（简化近似）
    if wait_syms:
        to_exec = sum(1 for s in wait_syms if s in exec_syms)
        stats["wait"]["to_exec"] = to_exec
        stats["wait_to_exec_rate_pct"] = round(to_exec / len(wait_syms) * 100, 2)
    return stats


# ────────────────────────────────────────────────────────────────
# outcome 回填（成交后评估后续行情，实盘收盘统一执行）
# ────────────────────────────────────────────────────────────────

def _assess_outcome(symbol: str, side: str, fill_price: float,
                    trade_day: Optional[str] = None,
                    lookahead_bars: int = 6) -> Optional[Dict[str, Any]]:
    """成交后评估：拉该标的当日 m5，统计成交后 lookahead_bars 根的实际走向。

    返回 outcome dict（防前视：只用成交 bar 之后的 bar）；数据不足返回 None。
    """
    try:
        from datetime import datetime, timedelta
        from app.services import t_data_sources as _tds
        day = trade_day or date.today().strftime("%Y-%m-%d")
        bars = _tds.fetch_tencent_mkline(_tds._normalize_symbol(symbol), freq="m5", count=320) or []
        # 过滤当日 bar，定位成交时刻之后
        day_bars = [b for b in bars if str(b.get("time", ""))[:10] == day]
        if not day_bars:
            return None
        # 找成交价附近第一根 bar（成交 bar 之后；容差 ±1.5% 吸收撮合价差）
        start_idx = None
        for i, b in enumerate(day_bars):
            try:
                if float(b.get("close") or 0) <= fill_price * 1.015 and \
                   float(b.get("close") or 0) >= fill_price * 0.985:
                    start_idx = i
                    break
            except (TypeError, ValueError):
                continue
        if start_idx is None:
            # 未精确匹配：取当日最后一根之前（保守：从倒数 lookahead 根起算不可靠 → 返回 None）
            return None
        # 评估窗口：成交 bar 之后 6~12 根（30-60 分钟）
        window = day_bars[start_idx + 1: start_idx + 1 + max(lookahead_bars, 12)]
        if len(window) < 3:
            return None
        # entry 用成交 bar 的 close（市场走向基准，不含滑点成本）
        entry = float(day_bars[start_idx].get("close") or 0) or fill_price
        exit_price = float(window[-1]["close"] or 0)
        pct = (exit_price - entry) / entry * 100 if entry else 0.0
        high = max(float(b.get("high") or 0) for b in window)
        low = min(float(b.get("low") or 0) for b in window)
        # 目标/止损近似：低吸目标 = 成交价×1.01，止损 = 成交价×0.985
        hit_target = high >= entry * 1.01 if side == "buy" else low <= entry * 0.99
        hit_stop = low <= entry * 0.985 if side == "buy" else high >= entry * 1.015
        return {
            "kind": "exec", "side": side, "fill_price": round(fill_price, 3),
            "entry_price": round(entry, 3),
            "exit_price": round(exit_price, 3), "bars_after": len(window),
            "direction": "up" if pct >= 0 else "down",
            "pct_change": round(pct, 3), "hit_target": bool(hit_target),
            "hit_stop": bool(hit_stop), "assessed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    except Exception as e:
        print(f"[t-ai-agent] outcome 评估失败 {symbol}: {str(e)[:120]}")
        return None


def record_outcome(symbol: Optional[str] = None, trade_date: Optional[str] = None) -> Dict[str, Any]:
    """回填 AI exec 成交记录的 outcome（当日未回填的 ai_exec 且网关成功）。

    由收盘复盘（ai_daily_review）统一调用；也可按标的单独触发。
    """
    td = trade_date or _today()
    actions = t_db.list_ai_actions(trade_date=td, symbol=symbol or None, limit=500)
    filled = 0
    skipped = 0
    for a in actions:
        if a.get("action_type") != "ai_exec":
            continue
        if a.get("outcome"):
            continue  # 已回填
        gw = a.get("gateway_result") or {}
        if gw.get("status") != "success":
            skipped += 1
            continue
        sym = a.get("symbol") or ""
        # 成交价：gateway_result 或 input_snapshot 中取
        fill_price = float(gw.get("price") or 0)
        if fill_price <= 0:
            inp = a.get("input_snapshot") or {}
            trig = (inp.get("trigger") or {}).get("suggest_bid_price") \
                or (inp.get("trigger") or {}).get("suggest_ask_price") or 0
            fill_price = float(trig or 0)
        if fill_price <= 0:
            skipped += 1
            continue
        out = a.get("output") or {}
        side = "sell" if str(out.get("side") or "") == "sell" else "buy"
        oc = _assess_outcome(sym, side, fill_price, trade_day=td)
        if oc is None:
            skipped += 1
            continue
        t_db.update_ai_action_outcome(a.get("id"), oc)
        filled += 1
    print(f"[t-ai-agent] outcome 回填: {filled} 条完成, {skipped} 条跳过（{td}）")
    return {"filled": filled, "skipped": skipped, "trade_date": td}


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
