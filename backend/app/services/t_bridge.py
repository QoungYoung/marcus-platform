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


def _recent_decisions(symbol: str, limit: int = 5) -> List[Dict[str, Any]]:
    """最近 N 次 AI 决策（t_ai_actions 倒序，含 outcome 结果摘要），唤醒上下文供 AI 参考历史判断。"""
    try:
        return t_db.list_ai_actions(symbol=symbol, limit=limit)
    except Exception:
        return []


def _symbol_t_stats(symbol: str) -> Dict[str, Any]:
    """该标的做T历史统计（供 AI 决策参考）：低吸触发后走向、exec 胜率、abandon 正确率。"""
    try:
        from app.services.t_ai_agent import decision_quality
        q = decision_quality(symbol=symbol)
        return {
            "total_decisions": q.get("total", 0),
            "exec_count": q.get("exec", {}).get("count", 0),
            "exec_win_rate_pct": q.get("exec_win_rate_pct"),
            "exec_avg_pct": q.get("exec", {}).get("avg_pct"),
            "abandon_count": q.get("abandon", {}).get("count", 0),
            "abandon_correct_rate_pct": q.get("abandon_correct_rate_pct"),
            "wait_count": q.get("wait", {}).get("count", 0),
            "wait_to_exec_rate_pct": q.get("wait_to_exec_rate_pct"),
        }
    except Exception as e:
        return {"error": str(e)[:100]}


def _outcome_summary(oc: Dict[str, Any]) -> str:
    """outcome 摘要（供唤醒上下文展示）：✅+0.85% / ⛔-1.5%。"""
    try:
        pct = float(oc.get("pct_change") or 0)
        direction = "✅" if pct > 0 else "⛔"
        return f"{direction}{pct:+.2f}%"
    except (TypeError, ValueError):
        return ""


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
    # 增强上下文：持仓摘要 + 最近决策（含结果） + 标的做T历史统计 + 连续命中计数
    ctx.setdefault("position", _position_summary(symbol))
    ctx.setdefault("recent_decisions", _recent_decisions(symbol))
    ctx.setdefault("symbol_t_stats", _symbol_t_stats(symbol))
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
    # 高抛卖腿是兑现利润的正向动作：连续命中告警不适用于高抛（高抛越多越好）
    if hit_alert and trigger.get("event_type") != "high_sell_then_buy_back":
        msg += (f"⚠️ 该条件已连续命中 {consec} 次且未见实质改善——你必须二选一："
                f"① 输出 update_condition 附新的合理条件；② 输出 wait 注明『连续命中，等待冷却』。"
                f"严禁只把 target_price 往现价方向微调制造下一轮触发。")
    msg += (
        f"你是做T决策者：本次触发已命中你的监控条件并通过系统规则预筛——**默认动作=exec（执行）**。"
        f"仅当存在客观证据时才 wait/abandon，且 reason 必须写明具体证据："
        f"① 现价与目标价/建议价脱节（差>1%）；② 已跌破止损；③ regime 禁自动；④ 恐慌放量追跌（量比骤升+创新低）。"
        f"信息不足不等于 wait——若快照缺现价/量能，请先调用查询工具补数再判。"
        f"输出决策 JSON："
        f'{{"action": "exec|wait|abandon|update_condition", "reason": "一句话理由", '
        f'"condition": {{...}}}}（condition 仅在 update_condition 时提供，含 symbol/trigger_kind/target_price 等）。'
        f"exec 将按触发快照的『建议价』执行（低吸用建议买价、高抛用建议卖价），数量由系统按可卖底仓自动裁定；"
        f"你不需要也不应自定价量，decision 只表达『是否放行』。如需改价，请用 update_condition 改写条件后让系统重新触发。"
        f"如需更多数据可调用查询工具（get_stock_quote 实时行情 / get_t_realtime_indicators 技术指标"
        f"/ get_intraday_minute 分钟K线 / get_portfolio_positions 持仓 / get_stock_moneyflow 资金流"
        f"/ get_market_state 大盘），不必只依赖本快照。"
    )
    # 历史模式段：最近决策结果 + 标的做T统计（决策 checklist 依据）
    recents = ctx.get("recent_decisions") or []
    if recents:
        lines = ["【历史决策参考（最近 " + str(len(recents)) + " 次，含结果）】"]
        for r in recents[:5]:
            at = r.get("action_type", "")
            oc = r.get("outcome") or {}
            oc_sum = _outcome_summary(oc) if oc else "（无结果）"
            rs = ((r.get("output") or {}).get("reason") or "")[:60]
            lines.append(f"- {at} {oc_sum} {rs}")
        msg += "\n".join(lines) + "\n"
    st = ctx.get("symbol_t_stats") or {}
    if st and st.get("total_decisions"):
        win_rate = st.get("exec_win_rate_pct")
        msg += (
            f"【{symbol} 做T历史统计】决策 {st.get('total_decisions')} 次 | "
            f"exec {st.get('exec_count')} 次 胜率 {win_rate}% "
            f"均幅 {st.get('exec_avg_pct')}% | "
            f"abandon {st.get('abandon_count')} 次 正确率 {st.get('abandon_correct_rate_pct')}% | "
            f"wait {st.get('wait_count')} 次 转exec {st.get('wait_to_exec_rate_pct')}%\n"
        )
        # 高胜率标的重触发放开（P3-3）：>55% 放开冷却；<40% 提示减仓
        if win_rate is not None and win_rate > 55:
            msg += f"【提示】该标的 exec 胜率 {win_rate}% > 55%，属于高胜率标的——允许连续命中继续触发（不强制冷却）。\n"
        elif win_rate is not None and win_rate < 40:
            msg += f"【警告】该标的 exec 胜率 {win_rate}% < 40%，历史表现差——建议减仓或收紧触发。\n"
    msg += (
        "【决策 checklist】① 价差/盈亏比（参考，非决定项）：现价距建议价应有 ≥0.2% 价差（网关建议层阈值），"
        "滑点+手续费不应吃光价差——网关仍会做最终风控（裸空/跌停/熔断/可卖底仓/单笔5%/回转额），"
        "你不需要比网关更严，系统一旦命中条件默认价差已够做；"
        "② 高抛卖腿（high_sell_then_buy_back）是兑现利润的正向动作——触及时应倾向 exec 卖出兑现，"
        "而非担心卖飞继续等待；③ 弹药：可卖底仓与浮盈浮亏（低吸触及时若亏损接近止损线才保守）；"
        "④ 历史模式：该标的低吸后历史走向/exec 胜率（仅作趋势参考，不作为否决依据——"
        "不要因为之前 wait 过就继续 wait）；"
        "⑤ 连续命中：低吸条件已达告警阈值可调整或等待冷却，高抛不适用冷却。"
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


# ────────────────────────────────────────────────────────────────
# AI 条件生成（AI 自主设定触发条件，系统条件命中后唤醒 AI 决策）
# ────────────────────────────────────────────────────────────────

# 条件生成结果缓存：key = (symbol, round(cost, 2), amp_med) → (conditions, source)
# 同一建仓参数不重复唤醒 LLM（滚动建仓每日多标的时显著降低 LLM 调用量）
_cond_gen_cache: Dict[tuple, tuple] = {}
# 允许 AI 生成条件关闭（灾难回退开关）
AI_CONDITIONS_ENABLED = True


def _bridge_base_url() -> str:
    """bridge 服务基址（去掉 /chat 路径段）——与 t_backtest_runner.bridge_base_url 同源。"""
    try:
        settings = get_settings()
        raw = getattr(settings, "PI_SERVER_URL", "http://127.0.0.1:3001/chat").rstrip("/")
    except Exception:
        raw = "http://127.0.0.1:3001/chat"
    scheme_sep = raw.find("://")
    if scheme_sep >= 0:
        rest = raw[scheme_sep + 3:]
        host = rest.split("/", 1)[0]
        return raw[:scheme_sep + 3] + host
    return raw.rsplit("/", 1)[0] if "/" in raw else raw


def generate_conditions(symbol: str, cost: float, amp_med: Optional[float] = None,
                        trend: Optional[dict] = None, regime: Optional[dict] = None,
                        context: Optional[dict] = None, session_id: Optional[str] = None,
                        use_cache: bool = True) -> Optional[Dict[str, Any]]:
    """AI 自主设定做T双条件（低吸 + 高抛回补）→ POST bridge /conditions/generate。

    返回 {"conditions": [...], "source": "ai"|"fallback", "reason": ...}；
    桥不可达 / AI 解析失败 / 开关关闭 → None（调用方回退规则公式 build_t_conditions）。
    """
    if not AI_CONDITIONS_ENABLED:
        return None
    cache_key = (symbol, round(float(cost), 2), round(float(amp_med), 3) if amp_med else None)
    if use_cache and cache_key in _cond_gen_cache:
        return dict(_cond_gen_cache[cache_key])  # 浅拷贝（conditions 列表引用可读）
    payload = {
        "symbol": symbol,
        "cost": float(cost),
        "amp_med": amp_med,
        "trend": trend,
        "regime": regime,
        "context": context,
        "session_id": session_id,
    }
    try:
        req = urllib.request.Request(
            _bridge_base_url() + "/conditions/generate",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        conditions = body.get("conditions") or []
        source = body.get("source") or "ai"
        if not conditions:
            return None
        result = {"conditions": conditions, "source": source,
                  "reason": body.get("reason") or "AI 生成"}
        if use_cache:
            _cond_gen_cache[cache_key] = result
        print(f"[t-bridge] AI 条件生成 {symbol} → {len(conditions)} 条 (source={source}, "
              f"cost={cost})")
        return result
    except Exception as e:
        print(f"[t-bridge] AI 条件生成失败 {symbol}: {e}（回退规则公式）")
        return None
