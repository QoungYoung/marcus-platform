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

def upsert_condition(cond: Dict[str, Any]) -> Optional[int]:
    """写入/更新一条做T条件（幂等，按 account+symbol+trigger_kind+trade_date 唯一）。

    cond 支持字段：symbol/trigger_kind/target_price/reinform_price/vol_ratio_thresh/
    benchmark_turnover_profile/stabilize_level/sell_target_price/stop_loss_price/
    time_stop_open/time_stop_close/start_time/end_time/regime_gate/status/armed/expression
    """
    try:
        db = SessionLocal()
        try:
            sql = text(
                """
                INSERT INTO t_conditions (
                    account_id, symbol, trade_date, trigger_kind,
                    target_price, reinform_price, vol_ratio_thresh,
                    benchmark_turnover_profile, stabilize_level,
                    sell_target_price, stop_loss_price,
                    time_stop_open, time_stop_close, start_time, end_time,
                    regime_gate, status, armed, expression,
                    publisher, session_id
                ) VALUES (
                    :account_id, :symbol, :trade_date, :trigger_kind,
                    :target_price, :reinform_price, :vol_ratio_thresh,
                    :benchmark_turnover_profile, :stabilize_level,
                    :sell_target_price, :stop_loss_price,
                    :time_stop_open, :time_stop_close, :start_time, :end_time,
                    :regime_gate, :status, :armed, :expression,
                    :publisher, :session_id
                )
                ON CONFLICT (account_id, symbol, trigger_kind, trade_date)
                DO UPDATE SET
                    target_price = EXCLUDED.target_price,
                    reinform_price = EXCLUDED.reinform_price,
                    vol_ratio_thresh = EXCLUDED.vol_ratio_thresh,
                    benchmark_turnover_profile = EXCLUDED.benchmark_turnover_profile,
                    stabilize_level = EXCLUDED.stabilize_level,
                    sell_target_price = EXCLUDED.sell_target_price,
                    stop_loss_price = EXCLUDED.stop_loss_price,
                    time_stop_open = EXCLUDED.time_stop_open,
                    time_stop_close = EXCLUDED.time_stop_close,
                    start_time = EXCLUDED.start_time,
                    end_time = EXCLUDED.end_time,
                    regime_gate = EXCLUDED.regime_gate,
                    status = EXCLUDED.status,
                    armed = EXCLUDED.armed,
                    expression = EXCLUDED.expression,
                    publisher = EXCLUDED.publisher,
                    session_id = EXCLUDED.session_id
                RETURNING id
                """
            )
            row = db.execute(sql, {
                "account_id": cond.get("account_id", ACCOUNT_T),
                "symbol": cond["symbol"],
                "trade_date": cond.get("trade_date", _today()),
                "trigger_kind": cond.get("trigger_kind", "low_buy"),
                "target_price": cond.get("target_price"),
                "reinform_price": cond.get("reinform_price"),
                "vol_ratio_thresh": cond.get("vol_ratio_thresh"),
                "benchmark_turnover_profile": _to_jsonb(cond.get("benchmark_turnover_profile")),
                "stabilize_level": cond.get("stabilize_level"),
                "sell_target_price": cond.get("sell_target_price"),
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
            }).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] upsert_condition 失败: {e}")
        return None


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
                "regime_gate, expression, status, publisher, session_id "
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


def count_today_builds(symbol: Optional[str] = None) -> int:
    """当日建仓笔数（不含 rejected/cancelled）；symbol 给定则统计单票当日建仓数。"""
    try:
        db = SessionLocal()
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            sql = ("SELECT COUNT(*) AS c FROM t_build_events "
                   "WHERE account_id = 't' AND status NOT IN ('rejected', 'cancelled') "
                   "AND to_char(created_at, 'YYYY-MM-DD') = :today")
            params: Dict[str, Any] = {"today": today}
            if symbol:
                sql += " AND symbol = :symbol"
                params["symbol"] = symbol
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
                     gateway_result: Optional[dict] = None) -> Optional[int]:
    """写入一条 AI 决策审计（幂等可追溯）。"""
    try:
        db = SessionLocal()
        try:
            row = db.execute(text(
                "INSERT INTO t_ai_actions (session_id, trade_date, symbol, action_type, "
                "input_snapshot, output, gateway_result) "
                "VALUES (:sid, :td, :sym, :atype, :inp, :out, :gw) RETURNING id"
            ), {
                "sid": session_id, "td": trade_date, "sym": symbol, "atype": action_type,
                "inp": _to_jsonb(input_snapshot or {}),
                "out": _to_jsonb(output or {}),
                "gw": _to_jsonb(gateway_result) if gateway_result is not None else None,
            }).fetchone()
            db.commit()
            return row[0] if row else None
        finally:
            db.close()
    except Exception as e:
        print(f"[t-db] insert_ai_action 失败: {e}")
        return None


def list_ai_actions(trade_date: Optional[str] = None, symbol: Optional[str] = None,
                    limit: int = 100) -> List[Dict[str, Any]]:
    """查询 AI 决策审计（按日期/标的过滤，倒序）。"""
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
            sql += " ORDER BY id DESC LIMIT :lim"
            params["lim"] = limit
            rows = db.execute(text(sql), params).mappings().all()
            out = []
            for r in rows:
                import json as _json
                d = dict(r)
                for k in ("input_snapshot", "output", "gateway_result"):
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
