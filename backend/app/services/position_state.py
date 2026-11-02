# -*- coding: utf-8 -*-
"""仓位状态 —— 给交易 agent 的“原仓/已卖/剩余/本轮预算”。

只读：paper_positions + paper_trades（当日），不改任何状态。
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text


def _code(symbol: str) -> str:
    s = str(symbol or "").upper().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    return "".join(ch for ch in s if ch.isdigit())


def build_position_state(symbol: str, account_id: str = "stock",
                         sellable: Optional[int] = None,
                         asof: Optional[str] = None) -> Dict[str, Any]:
    """返回仓位/减仓预算；取数失败返回 error，不抛。"""
    code = _code(symbol)
    day = (asof or datetime.now().strftime("%Y%m%d")).replace("-", "")
    out: Dict[str, Any] = {
        "symbol": symbol, "account_id": account_id or "stock", "date": day,
        "current_volume": 0, "sellable": int(sellable or 0) if sellable is not None else None,
        "original_volume": 0, "sold_today": 0, "bought_today": 0,
        "avg_price": None, "max_reduce_pct": 0.5,
        "max_reduce_total": 0, "remaining_reduce_budget": 0,
        "can_reduce": False, "today_trades": [],
    }
    try:
        from app.database import SessionLocal
        with SessionLocal() as db:
            params = {"a": out["account_id"], "code": code}
            pos = db.execute(text(
                "SELECT volume, avg_price FROM paper_positions "
                "WHERE account_id=:a AND REPLACE(UPPER(symbol),'.','') LIKE '%' || :code || '%' "
                "ORDER BY symbol LIMIT 1"), params).fetchone()
            if pos:
                out["current_volume"] = int(pos[0] or 0)
                out["avg_price"] = float(pos[1]) if pos[1] is not None else None
            rows = db.execute(text(
                "SELECT created_at, direction, price, volume, reason FROM paper_trades "
                "WHERE account_id=:a AND REPLACE(UPPER(symbol),'.','') LIKE '%' || :code || '%' "
                "AND COALESCE(voided,0)=0 AND created_at::date = :d "
                "ORDER BY created_at"), {"a": out["account_id"], "code": code, "d": day}).fetchall()
            sold = bought = 0
            acts = []
            for r in rows:
                direction = str(r[1] or "")
                vol = int(r[3] or 0)
                if direction.startswith("卖"):
                    sold += vol
                elif direction.startswith("买"):
                    bought += vol
                acts.append({"time": str(r[0])[:19], "side": direction,
                             "price": float(r[2] or 0), "volume": vol,
                             "reason": str(r[4] or "")[:160]})
            out.update({"sold_today": sold, "bought_today": bought,
                        "today_trades": acts,
                        "original_volume": max(out["current_volume"] + sold - bought, 0)})
        try:
            out["max_reduce_pct"] = float(os.getenv("WOLF_REDUCE_CAP_PCT", "0.5"))
        except Exception:
            out["max_reduce_pct"] = 0.5
        orig = int(out["original_volume"] or 0)
        out["max_reduce_total"] = (int(orig * out["max_reduce_pct"]) // 100) * 100
        out["remaining_reduce_budget"] = max(int(out["max_reduce_total"] - out["sold_today"]), 0)
        out["can_reduce"] = out["remaining_reduce_budget"] >= 100
        if out["sellable"] is None:
            out["sellable"] = out["current_volume"]
        return out
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        return out
