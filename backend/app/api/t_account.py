# -*- coding: utf-8 -*-
"""做T系统 · API 路由（/api/v1/t/...）。

提供：账户状态 / 三层池 / 条件列表与生成 / 触发事件流 / 人工确认 / 审计 / STOP_ALL。
由 API 进程（uvicorn）加载，Worker 侧数据（t_monitor/t_bridge）通过 PostgreSQL 共享。
"""
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.database import SessionLocal
from app.services import t_db
from app.services.t_expr import FIELD_REGISTRY, expression_summary, validate_expression
from app.services.t_gateway import gateway_execute, get_sellable_ledger, check_breakers
from app.services.t_pool import compute_three_tier_pool, generate_conditions_for_live_pool
from app.services.t_regime import compute_regime

router = APIRouter(prefix="/t", tags=["t-account-trading"])


@router.get("/fields")
def t_fields():
    """字段注册表：Agent 可监控的全部数据字段（自由表达式监控条件用）。"""
    fields = [
        {
            "field": name,
            "type": meta[0],
            "description": meta[1],
            "source": meta[2] if len(meta) > 2 else "",
        }
        for name, meta in sorted(FIELD_REGISTRY.items())
    ]
    return {
        "fields": fields,
        "ops": sorted(["and", "or", "not", ">", ">=", "<", "<=", "==", "!=", "in", "not_in", "between"]),
        "example": {
            "and": [
                {"field": "quote.current", "op": "<=", "value": 98},
                {"field": "vol_ratio", "op": ">=", "value": 1.5},
                {"field": "minute.m1.bounce", "op": "==", "value": True},
                {"field": "regime.state", "op": "in", "value": ["ACTIVE", "CAUTIOUS"]},
            ]
        },
        "note": "表达式只控制触发时机；触发后仍走网关风控（可卖底仓/跌停/STOP_ALL/限额）。",
    }


@router.post("/conditions")
def t_condition_create(cond: dict):
    """创建/更新一条做T条件（支持自由 expression）。

    示例 body:
    {
        "symbol": "600519",
        "trigger_kind": "custom",           # 自定义类型
        "expression": {"and": [{"field": "quote.current", "op": "<=", "value": 98}]},
        "sell_target_price": 101.5,
        "stop_loss_price": 95.0,
        "regime_gate": "ALLOWED"
    }
    无 expression 时回退默认复合确认逻辑（trigger_kind/target_price/vol_ratio_thresh...）。
    """
    # 校验必填
    if not cond.get("symbol"):
        raise HTTPException(status_code=400, detail="缺少 symbol")
    # 校验表达式（非法字段/操作符直接 400）
    expr = cond.get("expression")
    if expr is not None:
        try:
            validate_expression(expr)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"表达式非法: {e}")
    cond.setdefault("account_id", "t")
    cond.setdefault("trigger_kind", "custom")
    cid = t_db.upsert_condition(cond)
    if not cid:
        raise HTTPException(status_code=500, detail="条件写入失败")
    return {"success": True, "condition_id": cid, "expression_summary": expression_summary(expr)}


@router.get("/overview")
def t_overview():
    """做T账户总览：资金 / 持仓 / 可卖额度 / regime / 熔断。"""
    regime = compute_regime()
    ledger = get_sellable_ledger()
    breaker, why = check_breakers()
    # 资金
    from app.database import SessionLocal
    from sqlalchemy import text as _text
    db = SessionLocal()
    try:
        acct = db.execute(_text(
            "SELECT initial_capital, available_cash, frozen_cash FROM paper_account_info "
            "WHERE account_id = 't'"
        )).mappings().first()
    finally:
        db.close()
    return {
        "account_id": "t",
        "capital": dict(acct) if acct else {},
        "positions": list(ledger.values()),
        "sellable_ledger": ledger,
        "regime": regime,
        "breaker": {"triggered": breaker, "reason": why},
    }


@router.get("/pool")
def t_pool(regime: Optional[str] = None):
    """三层池视图。"""
    st = regime or compute_regime().get("regime", "ACTIVE")
    pool = compute_three_tier_pool(regime=st)
    return {"regime": st, "pool": pool}


@router.post("/conditions/generate")
def t_conditions_generate():
    """为做T实盘池标的生成当日条件（选股 Agent 能力，可手动触发）。"""
    regime = compute_regime().get("regime", "ACTIVE")
    created = generate_conditions_for_live_pool(regime=regime)
    return {"created": len(created), "conditions": created}


@router.get("/conditions")
def t_conditions(symbol: Optional[str] = None, trade_date: Optional[str] = None):
    """条件列表（含表达式摘要，供 Agent/前端显示）。"""
    conds = t_db.list_active_conditions(symbol=symbol, trade_date=trade_date)
    import json as _json
    for c in conds:
        expr = c.get("expression")
        if expr:
            if isinstance(expr, str):
                try:
                    expr = _json.loads(expr)
                except (ValueError, TypeError):
                    expr = None
            c["expression_summary"] = expression_summary(expr) if expr else "(无效表达式)"
        else:
            c["expression_summary"] = "(默认复合确认逻辑)"
    return {"conditions": conds}


@router.post("/conditions/{condition_id}/rearm")
def t_condition_rearm(condition_id: int):
    """重新武装条件（复归价回归后手动/自动）。"""
    ok = t_db.update_condition_state(condition_id, armed=1)
    return {"success": ok}


@router.get("/triggers")
def t_triggers(limit: int = 100, status: Optional[str] = None):
    """触发事件流。"""
    return {"triggers": t_db.list_triggers(limit=limit, status=status)}


@router.post("/triggers/{trigger_id}/confirm")
def t_trigger_confirm(trigger_id: int, action: str = "execute", price: Optional[float] = None):
    """人工确认入口（human_confirm 分支）：execute 放行 / cancel 取消。

    放行仍走网关（唯一放行者），不做绕过。
    """
    trig = None
    for t in t_db.list_triggers(limit=1000, status="human_confirm"):
        if int(t.get("id") or 0) == trigger_id:
            trig = t
            break
    if not trig:
        # 也允许确认 claimed/auto_ready 事件
        for t in t_db.list_triggers(limit=1000):
            if int(t.get("id") or 0) == trigger_id:
                trig = t
                break
    if not trig:
        raise HTTPException(status_code=404, detail=f"触发事件 #{trigger_id} 不存在")

    if action == "cancel":
        ok = t_db.update_trigger_status(trigger_id, "cancelled", reason="人工取消")
        return {"success": ok, "status": "cancelled"}

    # execute：按快照建议价执行（走网关）
    side = "buy" if trig.get("event_type") in ("low_buy", "panic_vibrate") else "sell"
    exec_price = price or float(trig.get("suggest_bid_price") or trig.get("quote_price") or 0)
    from app.services.t_gateway import get_sellable_ledger
    ledger = get_sellable_ledger()
    item = ledger.get(trig.get("symbol", ""))
    volume = max(int((item["sellable"] * 0.3 if item else 0)), 100) if item else 100
    volume = (volume // 100) * 100
    result = gateway_execute(
        symbol=trig.get("symbol", ""), side=side, price=exec_price, volume=volume,
        condition_id=trig.get("condition_id"), trigger_id=trigger_id,
        reason="人工确认执行",
    )
    return result


@router.get("/audit")
def t_audit(limit: int = 200):
    """审计日志：触发事件流（状态流转）+ 熔断 + 条件。"""
    triggers = t_db.list_triggers(limit=limit)
    daily = t_db.get_daily_state()
    risk = t_db.get_risk_state()
    return {
        "triggers": triggers,
        "daily_state": daily,
        "risk_state": risk,
    }


@router.post("/stop-all")
def t_stop_all(flag: bool = True, reason: str = ""):
    """STOP_ALL 总开关（人工）。"""
    ok = t_db.set_stop_all(flag, reason or "manual")
    return {"success": ok, "stop_all": flag}


@router.post("/daily/reset")
def t_daily_reset():
    """重置当日账本（跨日/测试用）。"""
    ok = t_db.upsert_daily_state({
        "daily_turnover_amount": 0,
        "net_turnover_shares": 0,
        "realized_pnl": 0,
        "buy_count": 0,
        "sell_count": 0,
        "risk_breaker": False,
        "breaker_reason": None,
    })
    return {"success": ok}


@router.get("/build/scan-results")
def t_build_scan_results(trade_date: Optional[str] = None):
    """每日自动选股结果（盘后选股写入，次日盘中执行建仓）。

    返回 t_build_scan_results 表中某交易日（默认今天）的候选，按分数降序。
    """
    import json as _json
    from datetime import datetime as _dt
    from sqlalchemy import text as _text
    td = trade_date or _dt.now().strftime("%Y-%m-%d")
    db = SessionLocal()
    try:
        rows = db.execute(_text(
            "SELECT id, trade_date, symbol, score, reasons, trend, status, built_at "
            "FROM t_build_scan_results WHERE trade_date = :td ORDER BY score DESC"
        ), {"td": td}).mappings().all()
        out = []
        for r in rows:
            try:
                reasons = _json.loads(r["reasons"]) if isinstance(r["reasons"], str) else (r["reasons"] or [])
            except (ValueError, TypeError):
                reasons = []
            out.append({
                "id": r["id"], "trade_date": r["trade_date"], "symbol": r["symbol"],
                "score": r["score"], "reasons": reasons, "trend": r["trend"],
                "status": r["status"], "built_at": str(r["built_at"]) if r["built_at"] else None,
            })
        return {"trade_date": td, "results": out}
    finally:
        db.close()


@router.get("/ai/actions")
def t_ai_actions(trade_date: Optional[str] = None, symbol: Optional[str] = None,
                      session_id: Optional[str] = None, limit: int = 50):
    """AI 主导做T决策审计列表（t_ai_actions）+ 决策质量聚合。session_id 形如 t-backtest-<task_id>。"""
    from app.services import t_db
    from app.services.t_ai_agent import decision_quality
    actions = t_db.list_ai_actions(trade_date=trade_date or None,
                                   symbol=symbol or None,
                                   session_id=session_id or None, limit=limit)
    quality = decision_quality(symbol=symbol or None, trade_date=trade_date or None,
                               actions=actions)
    return {"actions": actions, "count": len(actions), "quality": quality}


# ────────────────────────────────────────────────────────────────
# 底仓建仓（t-position-building）：选股 / 建仓 / 衔接 / 再平衡 / 审计 / 调额
# ────────────────────────────────────────────────────────────────

@router.post("/account/capital-adjust")
def t_capital_adjust(amount: float, reason: str = ""):
    """t 账户资金调额（净注入为正）。更新 paper_account_info 并写建仓审计。"""
    from sqlalchemy import text as _text
    if not amount or amount <= 0:
        raise HTTPException(status_code=400, detail="调额金额必须为正数")
    db = SessionLocal()
    try:
        row = db.execute(_text(
            "SELECT initial_capital, available_cash FROM paper_account_info WHERE account_id = 't'"
        )).mappings().first()
        if row is None:
            raise HTTPException(status_code=404, detail="t 账户资金记录不存在（先触发一次 t 账户撮合初始化）")
        new_initial = float(row["initial_capital"] or 0) + amount
        new_cash = float(row["available_cash"] or 0) + amount
        db.execute(_text(
            "UPDATE paper_account_info SET initial_capital = :ic, available_cash = :cash, "
            "frozen_cash = COALESCE(frozen_cash, 0), updated_at = now() WHERE account_id = 't'"
        ), {"ic": new_initial, "cash": new_cash})
        db.commit()
    finally:
        db.close()
    # 审计（t_build_events，event_type='capital_adjust'）
    from app.services import t_build
    t_build.log_capital_adjust(amount, reason)
    return {"success": True, "account_id": "t", "amount": amount, "initial_capital": round(new_initial, 2)}


@router.get("/build/candidates")
def t_build_candidates(source: str = "pool", limit: int = 20):
    """扫描建仓候选短名单（source: pool 候选池 / scan 全市场粗筛；user 需显式传 symbols）。"""
    from app.services import t_build
    if source == "user":
        raise HTTPException(status_code=400, detail="user 来源需调用 POST /t/build/scan 传入 symbols")
    cands = t_build.scan_t_candidates(limit=limit, source=source)
    return {"source": source, "candidates": cands, "count": len(cands)}


@router.post("/build/scan")
def t_build_scan(payload: dict):
    """用户指定标的列表的建仓打分（source=user）。body: {symbols: [...], limit?}"""
    from app.services import t_build
    symbols = payload.get("symbols") or []
    if not symbols:
        raise HTTPException(status_code=400, detail="缺少 symbols")
    limit = int(payload.get("limit") or len(symbols))
    results = []
    for sym in symbols[:limit]:
        try:
            results.append(t_build.build_score(str(sym), source="user"))
        except Exception as e:
            results.append({"symbol": str(sym), "score": 0.0, "pass_gate": False, "error": str(e)})
    results.sort(key=lambda x: x.get("score") or 0, reverse=True)
    return {"source": "user", "candidates": results, "count": len(results)}


@router.post("/build/position")
def t_build_position(payload: dict):
    """底仓建仓（Agent/人工）：body {symbol, price, volume?, reason, decision_source?,
    skip_timing?, force_human?}。走独立建仓网关（validate_build_position + build_gateway_execute）。"""
    from app.services import t_build
    symbol = payload.get("symbol")
    price = payload.get("price")
    if not symbol or not price:
        raise HTTPException(status_code=400, detail="缺少 symbol/price")
    volume = payload.get("volume")
    reason = payload.get("reason") or ""
    decision_source = payload.get("decision_source") or "agent"
    if decision_source not in ("agent", "human"):
        raise HTTPException(status_code=400, detail="decision_source 只能为 agent/human")
    result = t_build.build_t_position(
        symbol=str(symbol), price=float(price),
        volume=int(volume) if volume else None,
        reason=reason, decision_source=decision_source,
        skip_timing=bool(payload.get("skip_timing")),
        force_human=bool(payload.get("force_human")),
    )
    return result


@router.post("/build/events/{event_id}/confirm")
def t_build_event_confirm(event_id: int, action: str = "execute"):
    """建仓人工确认：execute 放行（走建仓网关撮合）/ cancel 取消。"""
    from app.services import t_build
    ev = t_db.get_build_event(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail=f"建仓事件 #{event_id} 不存在")
    if ev.get("status") != "human_confirm":
        raise HTTPException(status_code=400, detail=f"建仓事件状态为 {ev.get('status')}，不可人工确认")
    if action == "cancel":
        t_db.update_build_event(event_id, status="cancelled", reason="人工取消")
        return {"success": True, "status": "cancelled"}
    if action != "execute":
        raise HTTPException(status_code=400, detail="action 只能为 execute/cancel")
    # 人工放行：force_human=True 走网关撮合；保留原建仓档位
    # （vrebounce/trend_break 短线档单笔上限 30%，与 standard 档 10% 不同）
    build_mode = "standard"
    ev_reason = str(ev.get("reason") or "")
    if "vrebounce" in ev_reason:
        build_mode = "vrebounce"
    elif "trend_break" in ev_reason:
        build_mode = "trend_break"
    result = t_build.build_gateway_execute(
        symbol=ev.get("symbol", ""), price=float(ev.get("price") or 0),
        volume=int(ev.get("volume") or 0),
        reason=ev.get("reason") or "人工确认建仓",
        decision_source="human", event_id=event_id, force_human=True,
        build_mode=build_mode,
    )
    return result


@router.get("/build/events")
def t_build_events(limit: int = 100, status: Optional[str] = None,
                   symbol: Optional[str] = None):
    """建仓审计列表（独立于做T触发事件流）。"""
    return {"events": t_db.list_build_events(limit=limit, status=status, symbol=symbol)}


@router.post("/build/auto-gen")
def t_build_auto_gen():
    """手动触发：为 live 池缺失条件的标的补生成次日条件（盘后任务同逻辑）。"""
    from app.services import t_build
    created = t_build.auto_gen_conditions_for_live_pool()
    return {"created": created}


@router.post("/build/rebalance")
def t_build_rebalance():
    """手动触发底仓再平衡评估。"""
    from app.services import t_build
    actions = t_build.rebalance_floors()
    return {"actions": actions, "count": len(actions)}


@router.get("/build/overview")
def t_build_overview():
    """底仓总览：净值 / 底仓市值 / 三档上限 / 候选数 / 建仓服务状态。"""
    from app.services import t_build
    net = t_build.t_net_asset()
    total_floor, _ = t_build._positions_value()
    p = t_build._params()
    regime = t_build.compute_regime().get("regime", "ACTIVE")
    tier = t_build.REGIME_TIER.get(regime, "std")
    try:
        status = t_build.get_t_build_service_status()
    except Exception:
        status = {}
    return {
        "account_id": "t",
        "regime": regime,
        "tier": tier,
        "net_asset": round(net, 2),
        "total_floor_value": round(total_floor, 2),
        "total_floor_cap": round(net * p["total_floor_cap"].get(tier, 0.55), 2),
        "per_symbol_cap": round(net * p["per_symbol_cap"].get(tier, 0.15), 2),
        "single_order_cap": round(net * p["single_order_pct"].get(tier, 0.05), 2),
        "max_floor_symbols": p.get("max_floor_symbols"),
        "service": status,
    }


@router.get("/build/params")
def t_build_params_get():
    """建仓策略参数（分档初值 + 覆盖值）。"""
    from app.services import t_build
    return {"params": t_build._params()}


@router.post("/build/params")
def t_build_params_update(payload: dict):
    """覆盖建仓策略参数（P4 扫描标定用）。"""
    from app.services import t_build
    ok = t_db.update_build_params(payload)
    return {"success": ok, "params": t_build._params()}


@router.get("/trend-break/status")
def t_trend_break_status():
    """做T账户·趋势突破短线监控状态（只作用于 t 账户）。"""
    from app.services import t_trend_break
    return t_trend_break.get_status()


@router.post("/trend-break/scan")
def t_trend_break_scan():
    """手动触发一轮趋势突破日频扫描（只写 t 账户候选）。"""
    from app.services import t_trend_break
    hits = t_trend_break.scan_once()
    return {"hits": hits, "count": len(hits)}


@router.post("/trend-break/build")
def t_trend_break_build():
    """手动触发盘中实时复核 + trend_break 建仓（只动 t 账户资金）。"""
    from app.services import t_trend_break
    return {"results": t_trend_break.try_build_candidates()}


@router.post("/trend-break/exit-check")
def t_trend_break_exit():
    """手动触发短线出场检查（+5%/+8%/-5%/5日，只卖 t 账户）。"""
    from app.services import t_trend_break
    return {"results": t_trend_break.check_exits()}


# ── V反 短线（t-vrebounce-short-term，只作用于 t 账户）──
@router.get("/vrebounce/status")
def t_vrebounce_status():
    """做T账户·V反短线监控状态（只作用于 t 账户）。"""
    from app.services import t_vrebounce
    return t_vrebounce.get_status()


@router.post("/vrebounce/scan")
def t_vrebounce_scan():
    """手动触发一轮 V反 日频扫描（只写 t 账户候选）。"""
    from app.services import t_vrebounce
    hits = t_vrebounce.scan_once()
    return {"hits": hits, "count": len(hits)}


@router.post("/vrebounce/build")
def t_vrebounce_build():
    """手动触发盘中实时复核 + V反 建仓（只动 t 账户资金）。"""
    from app.services import t_vrebounce
    return {"results": t_vrebounce.try_build_candidates()}


@router.post("/vrebounce/exit-check")
def t_vrebounce_exit():
    """手动触发 V反 短线出场检查（+8%/-5%/12交易日，只卖 t 账户）。"""
    from app.services import t_vrebounce
    return {"results": t_vrebounce.check_exits()}


# ── 科技ETF 动量趋势（t-mom-etf，只作用于 t 账户）──
@router.get("/mom-etf/status")
def t_mom_etf_status():
    """做T账户·科技ETF动量趋势监控状态（只作用于 t 账户）。"""
    from app.services import t_mom_etf
    return t_mom_etf.get_status()


@router.post("/mom-etf/scan")
def t_mom_etf_scan():
    """手动触发一轮科技ETF动量趋势扫描（只写 t 账户候选）。"""
    from app.services import t_mom_etf
    hits = t_mom_etf.scan_once()
    return {"hits": hits, "count": len(hits)}


@router.post("/mom-etf/rebalance")
def t_mom_etf_rebalance():
    """手动触发双周调仓（卖出掉出TOP3、买入新目标，只动 t 账户）。"""
    from app.services import t_mom_etf
    return {"results": t_mom_etf.try_rebalance(force=True)}


@router.post("/mom-etf/exit-check")
def t_mom_etf_exit():
    """手动触发调仓日出场检查（无独立止损，只卖 t 账户）。"""
    from app.services import t_mom_etf
    return {"results": t_mom_etf.check_exits()}


def _etf_shortline_candidates(source: str, limit: int = 20):
    """短线候选列表（source 参数化：vreb_etf / mom_etf），回填 ETF 名称。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    from app.services.golden_pit_config import TECH_SECTOR_POOL
    name_map = {e["etf_code"]: e["name"] for e in TECH_SECTOR_POOL.values()}
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT symbol, score, reasons, trend, status, built_at, trade_date, created_at "
            "FROM t_build_scan_results WHERE source = :src "
            "AND trade_date = (SELECT max(trade_date) FROM t_build_scan_results WHERE source = :src) "
            "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'executed' THEN 1 "
            "WHEN 'blocked' THEN 2 ELSE 3 END, score DESC LIMIT :lim"
        ), {"src": source, "lim": limit}).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["name"] = name_map.get(d.get("symbol"), "")
            out.append(d)
        return {"candidates": out, "count": len(out)}
    finally:
        db.close()


@router.get("/vreb-etf/candidates")
def t_vreb_etf_candidates(limit: int = 20):
    """科技ETF V反 候选列表（t_build_scan_results source='vreb_etf'）。"""
    return _etf_shortline_candidates("vreb_etf", limit)


@router.get("/mom-etf/candidates")
def t_mom_etf_candidates(limit: int = 20):
    """科技ETF 动量趋势 目标组合候选（t_build_scan_results source='mom_etf'）。"""
    return _etf_shortline_candidates("mom_etf", limit)


# ── 科技ETF V反（t-vreb-etf，只作用于 t 账户）──
@router.get("/vreb-etf/status")
def t_vreb_etf_status():
    """做T账户·科技ETF V反监控状态（只作用于 t 账户）。"""
    from app.services import t_vreb_etf
    return t_vreb_etf.get_status()


@router.post("/vreb-etf/scan")
def t_vreb_etf_scan():
    """手动触发一轮科技ETF V反日频扫描（只写 t 账户候选）。"""
    from app.services import t_vreb_etf
    hits = t_vreb_etf.scan_once()
    return {"hits": hits, "count": len(hits)}


@router.post("/vreb-etf/build")
def t_vreb_etf_build():
    """手动触发盘中实时复核 + 科技ETF V反建仓（只动 t 账户资金）。"""
    from app.services import t_vreb_etf
    return {"results": t_vreb_etf.try_build_candidates()}


@router.post("/vreb-etf/exit-check")
def t_vreb_etf_exit():
    """手动触发科技ETF出场检查（+6%/-4%/8日，只卖 t 账户）。"""
    from app.services import t_vreb_etf
    return {"results": t_vreb_etf.check_exits()}


import time as _time

_STOCK_NAME_CACHE = {"at": 0.0, "map": {}}


def _stock_name_map() -> dict:
    """全市场股票 ts_code->name 缓存（6h TTL），用于 V反 候选回填中文名称。"""
    now = _time.time()
    if _STOCK_NAME_CACHE["map"] and (now - _STOCK_NAME_CACHE["at"]) < 6 * 3600:
        return _STOCK_NAME_CACHE["map"]
    try:
        from app.services.t_vrebounce import _get_pro
        pro = _get_pro()
        df = pro.stock_basic(list_status="L", fields="ts_code,name")
        m = {}
        for _, r in df.iterrows():
            tc = str(r["ts_code"])
            if "." not in tc:
                continue
            code, exch = tc.split(".")
            pre = "SH" if exch == "SH" else ("SZ" if exch == "SZ" else "BJ")
            m[pre + code] = str(r["name"])
        _STOCK_NAME_CACHE.update({"at": now, "map": m})
        return m
    except Exception:
        return _STOCK_NAME_CACHE["map"]


@router.get("/vrebounce/candidates")
def t_vrebounce_candidates(limit: int = 20, days: int = 7):
    """V反 候选列表：默认只返回最新一个交易日（trade_date=max）的 pending 候选；
    传 days=0 时返回近 N 天全部（含历史状态，去重按最新）。仅 t 账户。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        name_map = _stock_name_map()
        if days <= 0:
            rows = db.execute(text(
                "SELECT DISTINCT ON (symbol) symbol, score, reasons, trend, status, built_at, trade_date, created_at "
                "FROM t_build_scan_results WHERE source = 'vrebounce' "
                "ORDER BY symbol, created_at DESC LIMIT :lim"
            ), {"lim": limit}).mappings().all()
        else:
            rows = db.execute(text(
                "SELECT symbol, score, reasons, trend, status, built_at, trade_date, created_at "
                "FROM t_build_scan_results WHERE source = 'vrebounce' "
                "AND trade_date = (SELECT max(trade_date) FROM t_build_scan_results WHERE source = 'vrebounce') "
                "ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'executed' THEN 1 "
                "WHEN 'blocked' THEN 2 ELSE 3 END, score DESC LIMIT :lim"
            ), {"lim": limit}).mappings().all()
        out = []
        for r in rows:
            d = dict(r)
            d["name"] = name_map.get(d.get("symbol"), "")
            out.append(d)
        return {"candidates": out, "count": len(out)}
    finally:
        db.close()


@router.get("/vrebounce/events")
def t_vrebounce_events(limit: int = 20):
    """V反 建仓/平仓事件（t_build_events reason 含 vrebounce，仅 t 账户）。"""
    from sqlalchemy import text
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        rows = db.execute(text(
            "SELECT id, symbol, event_type, side, price, volume, amount, executed_price, "
            "decision_source, reason, status, created_at "
            "FROM t_build_events WHERE account_id = 't' AND reason LIKE '%vrebounce%' "
            "ORDER BY created_at DESC LIMIT :lim"
        ), {"lim": limit}).mappings().all()
        return {"events": [dict(r) for r in rows], "count": len(rows)}
    finally:
        db.close()