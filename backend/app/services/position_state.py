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
        "cycle_start": None, "entry_volume": 0, "sold_since_entry": 0,
        "max_reduce_cycle": 0, "remaining_cycle_budget": 0,
        "board_half_done": False,
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
            # 当前持仓周期：从最近一次“空仓→买入”起算，供板上减半做跨日累计
            try:
                all_rows = db.execute(text(
                    "SELECT created_at, direction, volume, reason FROM paper_trades "
                    "WHERE account_id=:a AND REPLACE(UPPER(symbol),'.','') LIKE '%' || :code || '%' "
                    "AND COALESCE(voided,0)=0 ORDER BY created_at"), params).fetchall()
                _pos = 0
                _entry_vol = _sold_cycle = 0
                _start = None
                _bh = False
                for r in all_rows:
                    _dir = str(r[1] or "")
                    _vol = int(r[2] or 0)
                    _rs = str(r[3] or "").lower()
                    if _dir.startswith("买"):
                        if _pos <= 0:
                            _start = str(r[0])[:10]
                            _entry_vol = _sold_cycle = 0
                            _bh = False
                        _pos += _vol
                        _entry_vol += _vol
                    elif _dir.startswith("卖"):
                        _pos -= _vol
                        _sold_cycle += _vol
                        if "board" in _rs or "板上" in _rs:
                            _bh = True
                out.update({"cycle_start": _start, "entry_volume": _entry_vol,
                            "sold_since_entry": _sold_cycle,
                            "board_half_done": _bh})
            except Exception:
                pass
        try:
            out["max_reduce_pct"] = float(os.getenv("WOLF_REDUCE_CAP_PCT", "0.5"))
        except Exception:
            out["max_reduce_pct"] = 0.5
        orig = int(out["original_volume"] or 0)
        out["max_reduce_total"] = (int(orig * out["max_reduce_pct"]) // 100) * 100
        out["remaining_reduce_budget"] = max(int(out["max_reduce_total"] - out["sold_today"]), 0)
        out["can_reduce"] = out["remaining_reduce_budget"] >= 100
        try:
            _cap = float(os.getenv("WOLF_REDUCE_CAP_PCT", "0.5"))
        except Exception:
            _cap = 0.5
        _entry = int(out.get("entry_volume") or out["original_volume"] or 0)
        out["max_reduce_cycle"] = (int(_entry * _cap) // 100) * 100
        out["remaining_cycle_budget"] = max(int(out["max_reduce_cycle"] - int(out.get("sold_since_entry") or 0)), 0)
        if out["sellable"] is None:
            out["sellable"] = out["current_volume"]
        return out
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        return out
