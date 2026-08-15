# -*- coding: utf-8 -*-
"""做T系统 · Worker 主动唤醒 + Agent 复核决策桥接。

依据 final-t-plan.md §④/§⑥ 与 spec t-monitor-trigger / t-execution-risk：
- Worker 命中后主动 POST bridge /chat 唤醒做T Agent（附触发上下文），Agent 不轮询
- 桥不可达降级低频轮询兜底；Worker 永不直接下单
- Agent 复核决策：读 t_triggers 快照 + 合理性判断（默认自动、异常升级 6 类）
"""
import json
import time
import urllib.request
from typing import Any, Dict, Optional

from app.config import get_settings
from app.services import t_db
from app.services.t_gateway import classify_escalation, gateway_execute
from app.services.t_regime import compute_regime

# 唤醒降级轮询（桥不可达兜底）
FALLBACK_POLL_INTERVAL = 30.0
# 本地条件单（卖出端秒级）在网关内通过 t_conditions 价位判断承载（见 t_monitor）

_agent_session: Dict[str, str] = {}


def _bridge_url() -> str:
    """bridge /chat 地址（PI_SERVER_URL 或默认）。"""
    try:
        settings = get_settings()
        return getattr(settings, "PI_SERVER_URL", "http://127.0.0.1:3001/chat").rstrip("/") + "/chat"
    except Exception:
        return "http://127.0.0.1:3001/chat"


def wake_agent(trigger: Dict[str, Any], context: Optional[dict] = None) -> bool:
    """Worker 命中后主动唤醒做T Agent（POST /chat，附触发上下文）。"""
    symbol = trigger.get("symbol", "")
    payload = {
        "message": (
            f"【做T触发】{symbol} {trigger.get('event_type', 'low_buy')} "
            f"触发价={trigger.get('trigger_price')} 现价={trigger.get('quote_price')} "
            f"建议买价={trigger.get('suggest_bid_price')} 建议卖价={trigger.get('suggest_ask_price')} "
            f"事件#{trigger.get('id')} 条件#{trigger.get('condition_id')}。"
            f"请复核该做T触发：检查量价合理性、当前 regime、可卖底仓，决定 auto 执行或升级人工。"
            f"上下文: {json.dumps(context or {}, ensure_ascii=False)[:500]}"
        ),
        "session_id": _agent_session.setdefault(symbol, f"t-agent-{symbol}"),
        "mode": "chat",
    }
    try:
        req = urllib.request.Request(
            _bridge_url(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
        print(f"[t-bridge] 唤醒 Agent 成功: {symbol} ({len(body)} bytes)")
        return True
    except Exception as e:
        print(f"[t-bridge] 唤醒 Agent 失败（降级轮询兜底）: {e}")
        return False


def agent_review_and_execute(trigger: Dict[str, Any]) -> Dict[str, Any]:
    """Agent 复核决策入口（由 bridge /chat 或降级轮询调用）。

    流程：读快照 → 合理性判断 → 异常升级分类 → 默认自动走网关 / 升级 human_confirm。
    Worker 永不直接下单：真正下单永远经 gateway_execute（唯一放行者）。
    """
    trigger_id = int(trigger.get("id") or 0)
    symbol = trigger.get("symbol", "")
    side = "buy" if trigger.get("event_type") in ("low_buy", "panic_vibrate") else "sell"
    regime = compute_regime().get("regime", "ACTIVE")

    # 1) 异常升级分类
    escalation, why = classify_escalation(symbol, side, trigger=trigger, regime=regime)
    if escalation == "human":
        t_db.update_trigger_status(trigger_id, "human_confirm", reason=why)
        return {"status": "human_confirm", "trigger_id": trigger_id, "reason": why}

    # 2) 建议价
    price = float(trigger.get("suggest_bid_price") or trigger.get("quote_price") or 0)
    if price <= 0:
        t_db.update_trigger_status(trigger_id, "blocked", reason="无有效建议价")
        return {"status": "blocked", "reason": "无有效建议价"}

    # 3) 量（低吸：取可卖底仓对应用量；简化：单笔 1/3 可卖底仓，受网关分档约束）
    from app.services.t_gateway import get_sellable_ledger
    ledger = get_sellable_ledger()
    item = ledger.get(symbol)
    volume = max(int((item["sellable"] if item else 0) * 0.3), 100) if item else 100
    volume = (volume // 100) * 100

    # 4) 网关执行（唯一放行者）
    result = gateway_execute(
        symbol=symbol, side=side, price=price, volume=volume,
        condition_id=trigger.get("condition_id"), trigger_id=trigger_id,
        reason=f"做T触发#{trigger_id}",
    )
    return result


def fallback_poll_loop(stop_event):
    """桥不可达时的低频轮询兜底：消费 pending 事件 → agent_review_and_execute。

    Worker 永不直接下单（执行仍经网关）；本函数仅作为唤醒桥不可达时的兜底通道。
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
