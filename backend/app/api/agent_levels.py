# -*- coding: utf-8 -*-
"""Agent 关键位/状态 API（Portfolio 页替换旧防御系统）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.database import SessionLocal

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])


def _loads(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v) if v else None
    except Exception:
        return None


def _quote(symbol: str) -> Optional[float]:
    try:
        from app.services.t_data_sources import fetch_quote_one
        q = fetch_quote_one(symbol) or {}
        v = float(q.get("current") or 0)
        return v if v > 0 else None
    except Exception:
        return None


@router.get("/agent-levels")
def agent_levels(account: str = Query("stock"), date: Optional[str] = Query(None)) -> Dict[str, Any]:
    """当前持仓的最近一次 agent 判定 + 关键位/反抽带状态。"""
    d = (date or datetime.now().strftime("%Y%m%d")).replace("-", "")
    out: Dict[str, Any] = {"date": d, "account": account, "generated_at": None,
                           "positions": [], "watches": [], "message": ""}
    db = SessionLocal()
    try:
        try:
            pos_rows = db.execute(text(
                "SELECT symbol, volume, avg_price FROM paper_positions "
                "WHERE account_id=:a AND volume>0 ORDER BY symbol"), {"a": account}).mappings().all()
        except Exception:
            pos_rows = []

        # 最近一次 agent 决策（每个 symbol 一条）
        ev = {}
        try:
            rows = db.execute(text("""
                SELECT DISTINCT ON (symbol) symbol, phase, payload, quote, created_at
                FROM stop_v3_events
                WHERE event_date=:d AND payload ? 'agent_decision'
                ORDER BY symbol, id DESC
            """), {"d": d}).mappings().all()
            for r in rows:
                ev[str(r["symbol"])] = r
                out["generated_at"] = str(r["created_at"])[:19]
        except Exception:
            ev = {}

        # watch 状态
        watches = []
        try:
            for r in db.execute(text("""
                SELECT id,symbol,zone_low,zone_high,break_price,target_pct,invalidation_level,status,source,updated_at
                FROM wolf_v3_rebound_watch WHERE event_date=:d
                ORDER BY id DESC
            """), {"d": d}).mappings().all():
                watches.append({"type": "rebound", **dict(r)})
            for r in db.execute(text("""
                SELECT id,symbol,level,mode,break_time,bar_count,last_bar_time,volume_ratio,status,updated_at
                FROM wolf_v3_intraday_watch WHERE event_date=:d
                ORDER BY id DESC
            """), {"d": d}).mappings().all():
                watches.append({"type": "intraday", **dict(r)})
        except Exception:
            pass
        out["watches"] = [{k: (str(v)[:19] if k == "updated_at" else v) for k, v in w.items()} for w in watches]

        for p in pos_rows:
            sym = str(p["symbol"])
            row = ev.get(sym)
            payload = _loads(row["payload"]) if row else {}
            dec = (payload or {}).get("agent_decision") or {}
            bt = dec.get("break_trigger") if isinstance(dec, dict) else None
            ns = dec.get("next_support") if isinstance(dec, dict) else None
            rz = dec.get("rebound_zone") if isinstance(dec, dict) else None
            cur = _quote(sym) or _f(row["quote"] if row else 0)
            rel = [w for w in watches if str(w.get("symbol")) == sym]
            status = "no_agent"
            if rel:
                status = str(rel[0].get("status") or "watching")
            elif dec:
                status = "agent"
            out["positions"].append({
                "symbol": sym, "volume": int(p.get("volume") or 0),
                "avg_price": float(p.get("avg_price") or 0), "current_price": cur,
                "status": status,
                "action_mode": dec.get("action_mode") if isinstance(dec, dict) else None,
                "break_trigger": bt, "next_support": ns, "rebound_zone": rz,
                "confidence": dec.get("confidence") if isinstance(dec, dict) else None,
                "no_confident_level": bool(dec.get("no_confident_level")) if isinstance(dec, dict) else False,
                "phase": row["phase"] if row else None,
                "updated_at": str(row["created_at"])[:19] if row else None,
                "source": "agent" if dec else "deterministic",
            })
        if not pos_rows:
            out["message"] = "今日无持仓，agent 未触发"
        elif not ev:
            out["message"] = "今日尚无 agent 判定（等待 09:45/10:30/14:30/14:55 窗口）"
        return out
    finally:
        db.close()


def _f(x: Any) -> float:
    try:
        return float(x or 0)
    except Exception:
        return 0.0
