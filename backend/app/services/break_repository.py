# -*- coding: utf-8 -*-
"""break_repository.py — 破位事件/动作账本读写（P4）。

只做持久化，不做判断；所有写入幂等（语义键唯一）。
表结构见 `backend/app/models/break_orm.py` / `database._apply_break_stop_migration`。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.database import SessionLocal
from app.models.break_orm import BreakAction, BreakEvent


def _asof(x: Any) -> Optional[date]:
    s = "".join(ch for ch in str(x or "") if ch.isdigit())[:8]
    if len(s) != 8:
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _f(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def _b(x: Any) -> str:
    return "1" if bool(x) else "0"


def _event_dict(r: BreakEvent) -> Dict[str, Any]:
    return {
        "id": r.id, "asof": r.asof.strftime("%Y%m%d") if r.asof else None,
        "symbol": r.symbol, "domain": r.domain, "level_type": r.level_type,
        "level_price": _f(r.level_price), "phase": r.phase, "status": r.status,
        "close": _f(r.close), "high": _f(r.high), "low": _f(r.low),
        "vol_ratio": _f(r.vol_ratio), "volume_branch": r.volume_branch,
        "wave_op": r.wave_op, "position_pnl": _f(r.position_pnl),
        "action": r.action, "target_pct": _f(r.target_pct),
        "reason": r.reason, "mode": r.mode, "data_warning": r.data_warning,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


def _action_dict(r: BreakAction) -> Dict[str, Any]:
    return {
        "id": r.id, "asof": r.asof.strftime("%Y%m%d") if r.asof else None,
        "symbol": r.symbol, "domain": r.domain, "severity": r.severity,
        "level_type": r.level_type, "action": r.action,
        "target_pct": _f(r.target_pct), "target_is_cap": r.target_is_cap == "1",
        "block_new_buy": r.block_new_buy == "1", "allow_zero": r.allow_zero == "1",
        "reason": r.reason, "invalidation": r.invalidation,
        "status": r.status, "trigger_id": r.trigger_id,
        "executed_at": r.executed_at.isoformat() if r.executed_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


class BreakRepository:
    """SQLAlchemy 实现；测试可替换成内存 fake。"""

    def record_event(self, ev: Dict[str, Any]) -> Optional[int]:
        d = _asof(ev.get("asof"))
        if d is None or not ev.get("symbol") or not ev.get("level_type"):
            return None
        status = str(ev.get("status") or "none")
        with SessionLocal() as db:
            row = (db.query(BreakEvent)
                   .filter(BreakEvent.asof == d, BreakEvent.symbol == ev["symbol"],
                           BreakEvent.level_type == ev["level_type"], BreakEvent.status == status)
                   .first())
            if row:
                return int(row.id)
            row = BreakEvent(
                asof=d, symbol=str(ev["symbol"]), domain=str(ev.get("domain") or "index"),
                level_type=str(ev["level_type"]), level_price=_f(ev.get("level_price")),
                phase=str(ev.get("phase") or "close"), status=status,
                close=_f(ev.get("close")), high=_f(ev.get("high")), low=_f(ev.get("low")),
                vol_ratio=_f(ev.get("vol_ratio")), volume_branch=ev.get("volume_branch"),
                wave_op=ev.get("wave_op"), position_pnl=_f(ev.get("position_pnl")),
                action=ev.get("action"), target_pct=_f(ev.get("target_pct")),
                reason=ev.get("reason"), mode=str(ev.get("mode") or "alert"),
                data_warning=ev.get("data_warning"),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return int(row.id)

    def record_action(self, act: Dict[str, Any]) -> Optional[int]:
        d = _asof(act.get("asof"))
        if d is None or not act.get("symbol") or not act.get("action"):
            return None
        with SessionLocal() as db:
            row = (db.query(BreakAction)
                   .filter(BreakAction.asof == d, BreakAction.symbol == act["symbol"],
                           BreakAction.action == act["action"])
                   .first())
            if row:
                return int(row.id)
            row = BreakAction(
                asof=d, symbol=str(act["symbol"]), domain=str(act.get("domain") or "index"),
                severity=str(act.get("severity") or ""), level_type=act.get("level_type"),
                action=str(act["action"]), target_pct=_f(act.get("target_pct")),
                target_is_cap=_b(act.get("target_is_cap", True)),
                block_new_buy=_b(act.get("block_new_buy", False)),
                allow_zero=_b(act.get("allow_zero", False)),
                reason=act.get("reason"), invalidation=act.get("invalidation"),
                status=str(act.get("status") or "pending"),
                trigger_id=act.get("trigger_id"),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return int(row.id)

    def update_action_status(self, action_id: int, status: str,
                             trigger_id: Optional[int] = None) -> bool:
        with SessionLocal() as db:
            row = db.query(BreakAction).filter(BreakAction.id == int(action_id)).first()
            if not row:
                return False
            row.status = str(status)
            if trigger_id is not None:
                row.trigger_id = int(trigger_id)
            if str(status) in ("executed", "blocked", "skipped"):
                row.executed_at = datetime.now()
            db.commit()
            return True

    def list_events(self, date8: Any = None, symbol: Optional[str] = None,
                    limit: int = 200) -> List[Dict[str, Any]]:
        d = _asof(date8)
        with SessionLocal() as db:
            q = db.query(BreakEvent)
            if d:
                q = q.filter(BreakEvent.asof == d)
            if symbol:
                q = q.filter(BreakEvent.symbol == str(symbol))
            rows = q.order_by(BreakEvent.id.desc()).limit(max(1, min(int(limit), 1000))).all()
            return [_event_dict(r) for r in rows]

    def list_actions(self, date8: Any = None, symbol: Optional[str] = None,
                     limit: int = 200) -> List[Dict[str, Any]]:
        d = _asof(date8)
        with SessionLocal() as db:
            q = db.query(BreakAction)
            if d:
                q = q.filter(BreakAction.asof == d)
            if symbol:
                q = q.filter(BreakAction.symbol == str(symbol))
            rows = q.order_by(BreakAction.id.desc()).limit(max(1, min(int(limit), 1000))).all()
            return [_action_dict(r) for r in rows]

    def latest(self, date8: Any = None) -> Dict[str, Any]:
        return {"date": str(date8 or ""), "events": self.list_events(date8),
                "actions": self.list_actions(date8)}
