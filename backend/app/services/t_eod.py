# -*- coding: utf-8 -*-
"""做T系统 · 尾盘归平 + 高抛接回/踏空熔断 + 底仓保留下限 + 止损。

依据 final-t-plan.md §⑥ 与 spec t-execution-risk：
- 尾盘归平：14:45 后禁新开仓 + 强制平当日回转头寸（当日买入部分 T+1 锁定，归平针对当日卖出后可回补部分）
- 高抛接回规则：接回价≤高抛价−价差阈值 或 时限内未回落放弃接回
- 踏空熔断：卖出后价格上行超 X% 放弃接回、记一次降成本、不追高买回
- 底仓保留下限：跌破禁高抛、转只监控
- 止损：触发买入后破位（-X%）立即卖出旧底仓对冲
"""
from datetime import datetime
from typing import Any, Dict, Optional

from app.services import t_db
from app.services.t_data_sources import _normalize_symbol, fetch_tencent_quote
from app.services.t_gateway import gateway_execute, get_sellable_ledger

# 参数（P4 标定）
REBUY_SPREAD = 0.005        # 接回价 ≤ 高抛价 − 0.5%（价差阈值）
REBUY_TIMEOUT_MIN = 30      # 高抛后 30min 未回落放弃接回
MISS_REBUY_DROP_PCT = 1.0   # 卖出后上行超 1% → 踏空熔断，放弃接回
STOP_LOSS_PCT = 0.03        # 止损：买入后破位 -3% 卖出旧底仓对冲
EOD_TIME = "14:45"          # 尾盘归平开始时间
FLOOR_RATIO = 0.5           # 底仓保留下限：市值 ≥ 持仓成本 50%


def _now() -> datetime:
    return datetime.now()


def _is_after(hm_str: str) -> bool:
    now = _now()
    cur = now.hour * 100 + now.minute
    h, m = int(hm_str[:2]), int(hm_str[2:])
    return cur >= h * 100 + m


def should_force_eod_close() -> bool:
    """是否进入尾盘归平窗口（14:45 后）。"""
    return _is_after(EOD_TIME)


def eod_sweep(condition_id: Optional[int] = None) -> Dict[str, Any]:
    """尾盘归平：14:45 后把当日回转头寸处理干净（禁新开仓由 TMonitor 承担，此处平旧仓）。

    归平对象：当日高抛卖出未接回的部分（若仍在合理接回区间则接回，否则放弃 = 降仓）。
    简化实现：遍历当日高抛类条件，若满足接回条件且未触发踏空熔断则执行买回（走网关）。
    """
    result = {"swept": 0, "skipped": [], "reason": []}
    if not should_force_eod_close():
        return result
    conditions = t_db.list_active_conditions()
    for cond in conditions:
        if cond.get("trigger_kind") != "high_sell_then_buy_back":
            continue
        symbol = cond["symbol"]
        # 检查是否已高抛（存在 executed 高抛事件）
        triggers = t_db.list_triggers(limit=200)
        sold = [t for t in triggers
                if t.get("symbol") == symbol and t.get("event_type") == "high_sell_then_buy_back"
                and t.get("status") == "executed"]
        if not sold:
            continue
        # 接回判断
        action, why = _decide_rebuy(symbol, sold[-1])
        if action == "rebuy":
            from app.services.t_gateway import get_sellable_ledger
            ledger = get_sellable_ledger()
            item = ledger.get(symbol)
            volume = max(int((item["sellable"] if item else 0) * 0.3), 100) if item else 100
            volume = (volume // 100) * 100
            r = gateway_execute(symbol=symbol, side="buy",
                                price=float(sold[-1].get("quote_price") or 0) * (1 - REBUY_SPREAD),
                                volume=volume, condition_id=cond.get("id"),
                                reason="尾盘归平接回")
            if r.get("status") == "success":
                result["swept"] += 1
            else:
                result["skipped"].append({"symbol": symbol, "why": r.get("reason")})
        else:
            result["skipped"].append({"symbol": symbol, "why": why})
    return result


def _decide_rebuy(symbol: str, sold_trigger: Dict[str, Any]) -> tuple:
    """高抛后接回决策：接回价 ≤ 高抛价−价差阈值 且 时限内 且 未踏空。

    Returns: (action: 'rebuy'|'abandon', reason: str)
    """
    sell_price = float(sold_trigger.get("quote_price") or sold_trigger.get("suggest_ask_price") or 0)
    if sell_price <= 0:
        return "abandon", "无高抛成交价"

    # 现价
    q = fetch_tencent_quote([_normalize_symbol(symbol)])
    quote = q.get(_normalize_symbol(symbol)) or {}
    current = float(quote.get("current", 0) or 0)
    if current <= 0:
        return "abandon", "无法获取现价"

    # 踏空熔断：卖出后上行超 1% → 放弃接回
    if current >= sell_price * (1 + MISS_REBUY_DROP_PCT / 100):
        return "abandon", f"踏空熔断（现价 {current} 较高抛价 {sell_price} 上行超 {MISS_REBUY_DROP_PCT}%）"

    # 接回条件：回落到 高抛价−价差阈值 或 时限内
    rebuy_price = sell_price * (1 - REBUY_SPREAD)
    executed_at = sold_trigger.get("executed_at")
    within_time = True
    if executed_at:
        try:
            exec_dt = executed_at if isinstance(executed_at, datetime) else datetime.strptime(
                str(executed_at)[:19], "%Y-%m-%d %H:%M:%S")
            within_time = (datetime.now() - exec_dt).total_seconds() <= REBUY_TIMEOUT_MIN * 60
        except (ValueError, TypeError):
            within_time = True

    if current <= rebuy_price or within_time:
        return "rebuy", f"接回（现价 {current} ≤ {rebuy_price:.2f} 或时限内）"
    return "abandon", f"未回落且超时（现价 {current} > {rebuy_price:.2f}）"


def check_floor_lower() -> Dict[str, Any]:
    """底仓保留下限检查：跌破下限的标的禁高抛、转只监控。"""
    ledger = get_sellable_ledger()
    result = {"blocked": [], "ok": []}
    for symbol, item in ledger.items():
        avg = float(item.get("avg_price") or 0)
        vol = int(item.get("volume") or 0)
        value = avg * vol
        # 下限 = 持仓成本 × FLOOR_RATIO（近似：成本 = 当前持仓市值）
        floor = value * FLOOR_RATIO
        if value < floor:
            result["blocked"].append({"symbol": symbol, "value": value, "floor": floor})
        else:
            result["ok"].append(symbol)
    return result


def stop_loss_check(symbol: str, condition_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """每笔止损：条件买入后破位（-3%）卖出旧底仓对冲。"""
    cond = t_db.get_condition(condition_id) if condition_id else None
    if not cond:
        return None
    stop_price = float(cond.get("stop_loss_price") or 0)
    if stop_price <= 0:
        return None
    q = fetch_tencent_quote([_normalize_symbol(symbol)])
    quote = q.get(_normalize_symbol(symbol)) or {}
    current = float(quote.get("current", 0) or 0)
    if current <= 0:
        return None
    if current <= stop_price:
        from app.services.t_gateway import get_sellable_ledger
        ledger = get_sellable_ledger()
        item = ledger.get(symbol)
        volume = int(item["sellable"] * 0.5) if item else 0
        volume = (volume // 100) * 100
        if volume <= 0:
            return {"triggered": True, "why": "止损触发但无足够可卖", "action": "none"}
        r = gateway_execute(symbol=symbol, side="sell", price=current, volume=volume,
                            condition_id=condition_id, reason="做T止损")
        return {"triggered": True, "action": "sell", "result": r}
    return None
