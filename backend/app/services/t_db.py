# -*- coding: utf-8 -*-
"""做T系统 — 数据访问层（t_conditions / t_triggers / t_regime_state / t_daily_state / t_risk_state）。

统一封装五张 t_* 表的读写，供 TMonitor / 网关 / Agent / API 复用。
所有函数幂等、容错（表不存在时降级返回空，不炸主流程）。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import text

from app.database import SessionLocal

ACCOUNT_T = "t"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ────────────────────────────────────────────────────────────────
# t_conditions
# ────────────────────────────────────────────────────────────────

def infer_custom_direction(expression: Any) -> str:
    """custom 条件未显式声明 direction 时的保守推断（迭代#58b）。

    仅对**价格类字段**（quote.current/change_pct/open/high/low/pre_close）的
    主比较方向推断：< / <= → buy（低吸式），> / >= → sell（高抛式）；
    and/or/not 组合仅当全部价格子条件同向才推断，否则返回 '' = 保持旧语义=卖。
    量能/技术/环境类字段（vol_ratio/minute/tech/regime 等）不参与方向推断。
    兜底场景：AI/Agent 重建条件未带 direction（早晨批量重建曾把方向全部丢成
    默认卖腿），此推断避免"买点表达式被当卖腿执行"。
    """
    _DIR_FIELDS = ("quote.current", "quote.change_pct",
                   "quote.open", "quote.high", "quote.low", "quote.pre_close")
    try:
        def walk(n: Any):
            if not isinstance(n, dict):
                return ""
            # 容器节点：{"and": [...]} / {"or": [...]} / {"not": ...}（键即操作符）
            for combo in ("and", "or", "not"):
                if combo in n:
                    kids = n[combo]
                    if not isinstance(kids, list):
                        kids = [kids]
                    dirs = [walk(k) for k in kids if isinstance(k, dict)]
                    dirs = [d for d in dirs if d]
                    if dirs and all(d == dirs[0] for d in dirs):
                        return dirs[0]
                    return ""
            # 叶子节点：{"op": ..., "field": ..., "value": ...}
            op = str(n.get("op") or "")
            field = str(n.get("field") or "")
            is_price = field in _DIR_FIELDS
            if op in ("<=", "<") and is_price:
                return "buy"
            if op in (">=", ">") and is_price:
                return "sell"
            return ""
        return walk(expression)
    except Exception:
        return ""


def upsert_condition(cond: Dict[str, Any]) -> Optional[int]:
    """写入/更新一条做T条件（幂等，按 account+symbol+trigger_kind+trade_date 唯一）。

    cond 支持字段：symbol/trigger_kind/target_price/reinform_price/vol_ratio_thresh/
    benchmark_turnover_profile/stabilize_level/sell_target_price/stop_loss_price/
    time_stop_open/time_stop_close/start_time/end_time/regime_gate/status/armed/expression
    direction（迭代#58）：custom 等自由类型显式方向 buy|sell；空 = 按 trigger_kind 默认。

    迭代#58g（防呆）：冲突更新**只写调用方提供的非 None 字段** + 恒重置
    status/armed（默认 active/1）。此前全量覆盖——AI/重建条件缺字段时会把
    既有 expression/direction 等抹成 NULL/默认，造成"一次次纠错"。
    """
    try:
        db = SessionLocal()
        try:
            sell_kinds = ("high_sell", "high_sell_then_buy_back", "high_only")
            values = {
                "account_id": cond.get("account_id", ACCOUNT_T),
                "symbol": cond["symbol"],
                "trade_date": cond.get("trade_date", _today()),
                "trigger_kind": str(cond.get("trigger_kind") or "low_buy"),
                "target_price": cond.get("target_price"),
                # 迭代#58f：低吸类行不承载高抛/复归价（语义归位；止损保留共用）
                "reinform_price": _normalize_leg_price(cond, sell_kinds, "reinform_price"),
                "vol_ratio_thresh": cond.get("vol_ratio_thresh"),
                "benchmark_turnover_profile": _to_jsonb(cond.get("benchmark_turnover_profile")),
                "stabilize_level": cond.get("stabilize_level"),
                "sell_target_price": _normalize_leg_price(cond, sell_kinds, "sell_target_price"),
                "stop_loss_price": cond.get("stop_loss_price"),
                "time_stop_open": cond.get("time_stop_open"),
                "time_stop_close": cond.get("time_stop_close"),
                "start_time": cond.get("start_time"),
                "end_time": cond.get("end_time"),
                "regime_gate": cond.get("regime_gate", "ALLOWED"),
                "status": cond.get("status", "active"),
                "armed": 1 if cond.get("armed", 1) else 0,
                "expression": _to_jsonb(cond.get("expression")),
                "publisher": cond.get("publisher", "rule"),
                "session_id": cond.get("session_id"),
                # 迭代#58b：custom 无显式 direction 时按表达式主比较方向推断
                "direction": _resolve_direction(cond),
            }
            all_cols = ["account_id", "symbol", "trade_date", "trigger_kind",
                        "target_price", "reinform_price", "vol_ratio_thresh",
                        "benchmark_turnover_profile", "stabilize_level",
                        "sell_target_price", "stop_loss_price",
                        "time_stop_open", "time_stop_close", "start_time", "end_time",
                        "regime_gate", "status", "armed", "expression",
                        "publisher", "session_id", "direction"]
            # 冲突更新列：提供的非 None 字段 + 恒有 status/armed（重激活语义）
            update_cols = [c for c in all_cols
                           if c in ("status", "armed") or values[c] is not None]
            set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            sql = text(
                f"""
                INSERT INTO t_conditions ({", ".join(all_cols)})
                VALUES ({", ".join(":" + c for c in all_cols)})
                ON CONFLICT (account_id, symbol, trigger_kind, trade_date)
                DO UPDATE SET {set_clause}
                RETURNING id
                """
            )
            row = db.execute(sql, values).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] upsert_condition 失败: {e}")
        return None


def _normalize_leg_price(cond: Dict[str, Any], sell_kinds: tuple, field: str):
    """迭代#58f：高抛/复归价仅卖腿（high_sell 等）承载，低吸类行强制置空。"""
    kind = str(cond.get("trigger_kind") or "")
    if kind not in sell_kinds:
        return None
    return cond.get(field)


def _resolve_direction(cond: Dict[str, Any]) -> str:
    """条件方向最终值：显式 direction 优先；custom 缺省按表达式推断；其余按类型默认（空）。

    存储空 = 运行时按 trigger_kind 默认（low_buy/panic_vibrate=买，其余=卖）。
    """
    d = (cond.get("direction") or "").strip().lower()[:8]
    if d in ("buy", "sell"):
        return d
    if str(cond.get("trigger_kind") or "") == "custom":
        return infer_custom_direction(cond.get("expression"))
    return ""


def list_active_conditions(symbol: Optional[str] = None, trade_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """列出当日有效条件（默认 status='active'）。"""
    try:
        db = SessionLocal()
        try:
            sql = (
                "SELECT id, account_id, symbol, trade_date, trigger_kind, "
                "target_price, reinform_price, vol_ratio_thresh, "
                "benchmark_turnover_profile, stabilize_level, "
                "sell_target_price, stop_loss_price, "
                "time_stop_open, time_stop_close, start_time, end_time, "
                "armed, armed_at, last_triggered_at, trigger_count_today, "
                "regime_gate, expression, status, publisher, session_id, direction "
                "FROM t_conditions WHERE status = 'active' AND trade_date = :trade_date"
            )
            params: Dict[str, Any] = {"trade_date": trade_date or _today()}
            if symbol:
                sql += " AND symbol = :symbol"
                params["symbol"] = symbol
            rows = db.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] list_active_conditions 失败: {e}")
        return []


def get_condition(condition_id: int) -> Optional[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(
                text("SELECT * FROM t_conditions WHERE id = :id"), {"id": condition_id}
            ).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] get_condition 失败: {e}")
        return None


def update_condition_state(condition_id: int, armed: Optional[int] = None,
                           last_triggered_at: Optional[str] = None,
                           trigger_count_today: Optional[int] = None,
                           status: Optional[str] = None) -> bool:
    """更新条件状态机（armed/触发计数/状态）。"""
    try:
        db = SessionLocal()
        try:
            sets = []
            params: Dict[str, Any] = {"id": condition_id}
            if armed is not None:
                sets.append("armed = :armed")
                params["armed"] = armed
            if last_triggered_at is not None:
                sets.append("last_triggered_at = :last_triggered_at")
                params["last_triggered_at"] = last_triggered_at
            if trigger_count_today is not None:
                sets.append("trigger_count_today = :trigger_count_today")
                params["trigger_count_today"] = trigger_count_today
            if status is not None:
                sets.append("status = :status")
                params["status"] = status
            if not sets:
                return True
            sql = f"UPDATE t_conditions SET {', '.join(sets)} WHERE id = :id"
            db.execute(text(sql), params)
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] update_condition_state 失败: {e}")
        return False


def expire_daily_conditions(trade_date: Optional[str] = None) -> int:
    """清理过期条件（非当日条件置 expired）。返回处理条数。"""
    try:
        db = SessionLocal()
        try:
            res = db.execute(text(
                "UPDATE t_conditions SET status = 'expired' "
                "WHERE status = 'active' AND trade_date < :trade_date"
            ), {"trade_date": trade_date or _today()})
            db.commit()
            return res.rowcount
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] expire_daily_conditions 失败: {e}")
        return 0


# ────────────────────────────────────────────────────────────────
# t_triggers
# ────────────────────────────────────────────────────────────────

def insert_trigger(trig: Dict[str, Any]) -> Optional[int]:
    """写入一条触发事件（status='pending'）。"""
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                """
                INSERT INTO t_triggers (
                    account_id, condition_id, symbol, event_type,
                    trigger_price, quote_price, suggest_bid_price, suggest_ask_price,
                    slippage_budget, snapshot, status, mode, reason
                ) VALUES (
                    :account_id, :condition_id, :symbol, :event_type,
                    :trigger_price, :quote_price, :suggest_bid_price, :suggest_ask_price,
                    :slippage_budget, :snapshot, 'pending', :mode, :reason
                )
                RETURNING id
                """
            ), {
                "account_id": trig.get("account_id", ACCOUNT_T),
                "condition_id": trig.get("condition_id"),
                "symbol": trig["symbol"],
                "event_type": trig.get("event_type", "low_buy"),
                "trigger_price": trig.get("trigger_price"),
                "quote_price": trig.get("quote_price"),
                "suggest_bid_price": trig.get("suggest_bid_price"),
                "suggest_ask_price": trig.get("suggest_ask_price"),
                "slippage_budget": trig.get("slippage_budget"),
                "snapshot": _to_jsonb(trig.get("snapshot")),
                "mode": trig.get("mode", "auto"),
                "reason": trig.get("reason"),
            }).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] insert_trigger 失败: {e}")
        return None


def claim_pending_trigger(consumer: str, timeout_seconds: int = 300) -> Optional[Dict[str, Any]]:
    """原子消费一条 pending 事件（UPDATE...WHERE status='pending' RETURNING，防多消费者重复）。

    同时把超过 timeout 的 pending 事件按孤儿单处理置 cancelled（见孤儿单处置）。
    """
    try:
        db = SessionLocal()
        try:
            # 孤儿单处置：pending 且创建超过 timeout 未认领 → cancelled
            cutoff = datetime.now() - timedelta(seconds=timeout_seconds)
            db.execute(text(
                "UPDATE t_triggers SET status = 'cancelled', reason = 'orphan_timeout' "
                "WHERE status = 'pending' AND created_at < :cutoff"
            ), {"cutoff": cutoff})
            row = db.execute(text(
                """
                UPDATE t_triggers SET status = 'claimed', claimed_by = :consumer, claimed_at = now()
                WHERE id = (
                    SELECT id FROM t_triggers
                    WHERE status = 'pending'
                    ORDER BY id LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, account_id, condition_id, symbol, event_type,
                          trigger_price, quote_price, suggest_bid_price, suggest_ask_price,
                          slippage_budget, snapshot, mode, status
                """
            ), {"consumer": consumer}).mappings().first()
            db.commit()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] claim_pending_trigger 失败: {e}")
        return None


def update_trigger_status(trigger_id: int, status: str, reason: Optional[str] = None,
                          executed_price: Optional[float] = None) -> bool:
    """更新事件状态（auto_ready/human_confirm/executed/blocked/cancelled）。"""
    try:
        db = SessionLocal()
        try:
            sql = "UPDATE t_triggers SET status = :status, reason = :reason"
            params: Dict[str, Any] = {"status": status, "reason": reason, "id": trigger_id}
            if status == "executed":
                sql += ", executed_at = now()"
            if executed_price is not None:
                sql += ", quote_price = :executed_price"
                params["executed_price"] = executed_price
            sql += " WHERE id = :id"
            db.execute(text(sql), params)
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] update_trigger_status 失败: {e}")
        return False


def list_triggers(limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """查询触发事件流（供 API/前端/审计）。"""
    try:
        db = SessionLocal()
        try:
            sql = "SELECT * FROM t_triggers"
            params: Dict[str, Any] = {}
            if status:
                sql += " WHERE status = :status"
                params["status"] = status
            sql += " ORDER BY id DESC LIMIT :limit"
            params["limit"] = limit
            rows = db.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] list_triggers 失败: {e}")
        return []


# ────────────────────────────────────────────────────────────────
# t_regime_state
# ────────────────────────────────────────────────────────────────

def get_regime_state(trade_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT * FROM t_regime_state WHERE trade_date = :trade_date"
            ), {"trade_date": trade_date or _today()}).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] get_regime_state 失败: {e}")
        return None


def upsert_regime_state(state: Dict[str, Any]) -> bool:
    """写入/更新当日环境闸门状态。"""
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                """
                INSERT INTO t_regime_state (
                    trade_date, regime, daily_source, updated_at_daily,
                    intraday_lowbias, intraday_index_drop, intraday_updated,
                    gate_low_buy, gate_high_sell, gate_interpret_sign
                ) VALUES (
                    :trade_date, :regime, :daily_source, :updated_at_daily,
                    :intraday_lowbias, :intraday_index_drop, :intraday_updated,
                    :gate_low_buy, :gate_high_sell, :gate_interpret_sign
                )
                ON CONFLICT (trade_date) DO UPDATE SET
                    regime = EXCLUDED.regime,
                    daily_source = EXCLUDED.daily_source,
                    updated_at_daily = EXCLUDED.updated_at_daily,
                    intraday_lowbias = EXCLUDED.intraday_lowbias,
                    intraday_index_drop = EXCLUDED.intraday_index_drop,
                    intraday_updated = EXCLUDED.intraday_updated,
                    gate_low_buy = EXCLUDED.gate_low_buy,
                    gate_high_sell = EXCLUDED.gate_high_sell,
                    gate_interpret_sign = EXCLUDED.gate_interpret_sign
                """
            ), {
                "trade_date": state.get("trade_date", _today()),
                "regime": state.get("regime", "ACTIVE"),
                "daily_source": state.get("daily_source", "market_diagnosis"),
                "updated_at_daily": state.get("updated_at_daily", _now()),
                "intraday_lowbias": bool(state.get("intraday_lowbias", False)),
                "intraday_index_drop": state.get("intraday_index_drop", 0),
                "intraday_updated": state.get("intraday_updated"),
                "gate_low_buy": state.get("gate_low_buy", "ALLOWED"),
                "gate_high_sell": state.get("gate_high_sell", "ALLOWED"),
                "gate_interpret_sign": state.get("gate_interpret_sign", 1),
            })
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] upsert_regime_state 失败: {e}")
        return False


# ────────────────────────────────────────────────────────────────
# t_daily_state / t_risk_state
# ────────────────────────────────────────────────────────────────

def get_daily_state(trade_date: Optional[str] = None) -> Optional[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT * FROM t_daily_state WHERE account_id = 't' AND trade_date = :trade_date"
            ), {"trade_date": trade_date or _today()}).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] get_daily_state 失败: {e}")
        return None


def upsert_daily_state(state: Dict[str, Any]) -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                """
                INSERT INTO t_daily_state (
                    account_id, trade_date, daily_turnover_amount, net_turnover_shares,
                    realized_pnl, buy_count, sell_count, risk_breaker, breaker_reason, updated_at
                ) VALUES (
                    't', :trade_date, :daily_turnover_amount, :net_turnover_shares,
                    :realized_pnl, :buy_count, :sell_count, :risk_breaker, :breaker_reason, now()
                )
                ON CONFLICT (account_id, trade_date) DO UPDATE SET
                    daily_turnover_amount = EXCLUDED.daily_turnover_amount,
                    net_turnover_shares = EXCLUDED.net_turnover_shares,
                    realized_pnl = EXCLUDED.realized_pnl,
                    buy_count = EXCLUDED.buy_count,
                    sell_count = EXCLUDED.sell_count,
                    risk_breaker = EXCLUDED.risk_breaker,
                    breaker_reason = EXCLUDED.breaker_reason,
                    updated_at = now()
                """
            ), {
                "trade_date": state.get("trade_date", _today()),
                "daily_turnover_amount": state.get("daily_turnover_amount", 0),
                "net_turnover_shares": state.get("net_turnover_shares", 0),
                "realized_pnl": state.get("realized_pnl", 0),
                "buy_count": state.get("buy_count", 0),
                "sell_count": state.get("sell_count", 0),
                "risk_breaker": bool(state.get("risk_breaker", False)),
                "breaker_reason": state.get("breaker_reason"),
            })
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] upsert_daily_state 失败: {e}")
        return False


def get_risk_state() -> Optional[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text("SELECT * FROM t_risk_state WHERE id = 1")).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] get_risk_state 失败: {e}")
        return None


def set_stop_all(flag: bool, reason: str = "") -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_risk_state SET stop_all = :flag, lock_reason = :reason, updated_at = now() WHERE id = 1"
            ), {"flag": flag, "reason": reason})
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] set_stop_all 失败: {e}")
        return False


def bump_consecutive_losses(delta: int = 1) -> bool:
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_risk_state SET consecutive_losses = GREATEST(0, consecutive_losses + :delta), updated_at = now() WHERE id = 1"
            ), {"delta": delta})
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] bump_consecutive_losses 失败: {e}")
        return False


# ────────────────────────────────────────────────────────────────
# t_build_events（底仓建仓审计/状态流，独立于 t_triggers 做T事件流）
# ────────────────────────────────────────────────────────────────

def insert_build_event(ev: Dict[str, Any]) -> Optional[int]:
    """写入一条建仓事件（默认 status='pending_confirmation'）。"""
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                """
                INSERT INTO t_build_events (
                    account_id, symbol, event_type, side, price, volume, amount,
                    executed_price, decision_source, reason, regime, gateway_result,
                    position_before, position_after, status
                ) VALUES (
                    :account_id, :symbol, :event_type, :side, :price, :volume, :amount,
                    :executed_price, :decision_source, :reason, :regime, :gateway_result,
                    :position_before, :position_after, :status
                )
                RETURNING id
                """
            ), {
                "account_id": ev.get("account_id", ACCOUNT_T),
                "symbol": ev["symbol"],
                "event_type": ev.get("event_type", "build_position"),
                "side": ev.get("side", "buy"),
                "price": ev.get("price"),
                "volume": ev.get("volume"),
                "amount": ev.get("amount"),
                "executed_price": ev.get("executed_price"),
                "decision_source": ev.get("decision_source", "agent"),
                "reason": ev.get("reason"),
                "regime": ev.get("regime"),
                "gateway_result": _to_jsonb(ev.get("gateway_result")),
                "position_before": _to_jsonb(ev.get("position_before")),
                "position_after": _to_jsonb(ev.get("position_after")),
                "status": ev.get("status", "pending_confirmation"),
            }).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] insert_build_event 失败: {e}")
        return None


def update_build_event(event_id: int, status: Optional[str] = None,
                       executed_price: Optional[float] = None, reason: Optional[str] = None) -> bool:
    """更新建仓事件状态（pending_confirmation→executed|rejected|cancelled / 人工确认）。"""
    try:
        db = SessionLocal()
        try:
            sets = ["updated_at = now()"]
            params: Dict[str, Any] = {"id": event_id}
            if status is not None:
                sets.append("status = :status")
                params["status"] = status
            if executed_price is not None:
                sets.append("executed_price = :executed_price")
                params["executed_price"] = executed_price
            if reason is not None:
                sets.append("reason = :reason")
                params["reason"] = reason
            db.execute(text(
                f"UPDATE t_build_events SET {', '.join(sets)} WHERE id = :id"
            ), params)
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] update_build_event 失败: {e}")
        return False


def get_build_event(event_id: int) -> Optional[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT * FROM t_build_events WHERE id = :id"
            ), {"id": event_id}).mappings().first()
            return dict(row) if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] get_build_event 失败: {e}")
        return None


def list_build_events(limit: int = 100, status: Optional[str] = None,
                      symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    try:
        db = SessionLocal()
        try:
            sql = "SELECT * FROM t_build_events WHERE account_id = 't'"
            params: Dict[str, Any] = {}
            if status:
                sql += " AND status = :status"
                params["status"] = status
            if symbol:
                sql += " AND symbol = :symbol"
                params["symbol"] = symbol
            sql += " ORDER BY id DESC LIMIT :limit"
            params["limit"] = limit
            rows = db.execute(text(sql), params).mappings().all()
            return [dict(r) for r in rows]
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] list_build_events 失败: {e}")
        return []


def count_today_builds(symbol: Optional[str] = None, exclude_id: Optional[int] = None) -> int:
    """当日建仓笔数（仅已成交 executed 的 build_position）；symbol 给定则统计单票当日建仓数。

    人工确认已移除：未成交事件（pending/human_confirm/rejected/cancelled）均不消耗
    当日配额，避免升级/待确认事件卡住后毒化全天建仓名额（08-25 生产事故根因）。
    只统计 event_type='build_position'（资金调额等非建仓事件不占配额）。

    exclude_id：排除指定事件（审计先行的"本次尝试"不应计入单票单批，避免把自己数进去）。
    """
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            sql = ("SELECT COUNT(*) AS c FROM t_build_events "
                   "WHERE account_id = 't' AND status = 'executed' "
                   "AND event_type = 'build_position' "
                   "AND to_char(created_at, 'YYYY-MM-DD') = :today")
            params: Dict[str, Any] = {"today": today}
            if symbol:
                sql += " AND symbol = :symbol"
                params["symbol"] = symbol
            if exclude_id is not None:
                sql += " AND id != :eid"
                params["eid"] = exclude_id
            row = db.execute(text(sql), params).mappings().first()
            return int(row["c"]) if row else 0
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] count_today_builds 失败: {e}")
        return 0


# ────────────────────────────────────────────────────────────────
# t_build_params（建仓策略参数，分档初值 + P4 敏感度扫描）
# ────────────────────────────────────────────────────────────────

def get_build_params() -> Dict[str, Any]:
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "SELECT params_json FROM t_build_params WHERE id = 1"
            )).mappings().first()
            if not row or not row.get("params_json"):
                return {}
            import json as _json
            p = row["params_json"]
            return _json.loads(p) if isinstance(p, str) else dict(p or {})
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] get_build_params 失败: {e}")
        return {}


def update_build_params(params: Dict[str, Any]) -> bool:
    try:
        db = SessionLocal()
        try:
            import json as _json
            db.execute(text(
                "UPDATE t_build_params SET params_json = :params, updated_at = now() WHERE id = 1"
            ), {"params": _json.dumps(params, ensure_ascii=False)})
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] update_build_params 失败: {e}")
        return False


# ────────────────────────────────────────────────────────────────
# 工具
# ────────────────────────────────────────────────────────────────

def _to_jsonb(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, (dict, list)):
        import json as _json
        return _json.dumps(obj, ensure_ascii=False)
    return obj


# ────────────────────────────────────────────────────────────────
# t_ai_actions（AI 主导做T决策审计）
# ────────────────────────────────────────────────────────────────

def insert_ai_action(session_id: Optional[str], trade_date: str, symbol: str,
                     action_type: str, input_snapshot: Optional[dict] = None,
                     output: Optional[dict] = None,
                     gateway_result: Optional[dict] = None,
                     outcome: Optional[dict] = None) -> Optional[int]:
    """写入一条 AI 决策审计（幂等可追溯）。outcome 为成交后回填的结果（可稍后 update）。"""
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "INSERT INTO t_ai_actions (session_id, trade_date, symbol, action_type, "
                "input_snapshot, output, gateway_result, outcome) "
                "VALUES (:sid, :td, :sym, :atype, :inp, :out, :gw, :oc) RETURNING id"
            ), {
                "sid": session_id, "td": trade_date, "sym": symbol, "atype": action_type,
                "inp": _to_jsonb(input_snapshot or {}),
                "out": _to_jsonb(output or {}),
                "gw": _to_jsonb(gateway_result) if gateway_result is not None else None,
                "oc": _to_jsonb(outcome) if outcome is not None else None,
            }).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] insert_ai_action 失败: {e}")
        return None


def update_ai_action_outcome(action_id: Optional[int], outcome: Dict[str, Any]) -> bool:
    """回填决策结果（成交后）：outcome JSONB。"""
    if not action_id:
        return False
    try:
        db = SessionLocal()
        try:
            db.execute(text(
                "UPDATE t_ai_actions SET outcome = :oc WHERE id = :id"
            ), {"oc": _to_jsonb(outcome), "id": action_id})
            db.commit()
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] update_ai_action_outcome 失败: {e}")
        return False


def list_ai_actions(trade_date: Optional[str] = None, symbol: Optional[str] = None,
                    session_id: Optional[str] = None,
                    limit: int = 100) -> List[Dict[str, Any]]:
    """查询 AI 决策审计（按日期/标的/会话过滤，倒序）。会话形如 t-backtest-<task_id>。"""
    try:
        db = SessionLocal()
        try:
            sql = "SELECT * FROM t_ai_actions WHERE 1=1"
            params: Dict[str, Any] = {}
            if trade_date:
                sql += " AND trade_date = :td"
                params["td"] = trade_date
            if symbol:
                sql += " AND symbol = :sym"
                params["sym"] = symbol
            if session_id:
                sql += " AND session_id = :sid"
                params["sid"] = session_id
            sql += " ORDER BY id DESC LIMIT :lim"
            params["lim"] = limit
            rows = db.execute(text(sql), params).mappings().all()
            out = []
            for r in rows:
                import json as _json
                d = dict(r)
                for k in ("input_snapshot", "output", "gateway_result", "outcome"):
                    v = d.get(k)
                    if isinstance(v, str):
                        try:
                            d[k] = _json.loads(v)
                        except (ValueError, TypeError):
                            pass
                out.append(d)
            return out
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] list_ai_actions 失败: {e}")
        return []
