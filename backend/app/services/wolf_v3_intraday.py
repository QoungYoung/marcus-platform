# -*- coding: utf-8 -*-
"""wolf_v3_intraday.py — 30s 轮询驱动的 3×15 / 放量破位检测。

只做纯检测 + PG 状态；执行仍由 t_monitor / wolf_v3_production 负责。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.services import decision_window as DW


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _dt(s: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(s)[:16], "%Y-%m-%d %H:%M")
    except Exception:
        return None


def completed_15m(bars5: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """聚合已完成 15min bar；未走完的 slot 不返回。"""
    now = now or datetime.now()
    slots: Dict[str, Dict[str, Any]] = {}
    for b in bars5 or []:
        t = _dt(b.get("trade_time") or b.get("time"))
        if t is None:
            continue
        end_min = ((t.minute // 15) + 1) * 15
        end = t.replace(minute=0, second=0, microsecond=0)
        if end_min >= 60:
            end = end.replace(hour=end.hour + 1, minute=0)
        else:
            end = end.replace(minute=end_min)
        if end > now:
            continue
        key = end.strftime("%Y-%m-%d %H:%M")
        o, h, l, c, v = _f(b.get("open")), _f(b.get("high")), _f(b.get("low")), _f(b.get("close")), _f(b.get("vol"))
        if key not in slots:
            slots[key] = {"trade_time": key, "open": o, "high": h, "low": l, "close": c, "vol": v}
        else:
            x = slots[key]
            x["high"] = max(x["high"], h)
            x["low"] = min(x["low"], l)
            x["close"] = c
            x["vol"] += v
    return [slots[k] for k in sorted(slots)]


def detect_3x15(bars5: List[Dict[str, Any]], level: float, now: Optional[datetime] = None) -> Dict[str, Any]:
    """收盘跌破 level 后，连续 3 根已完成 15min 都收在 level 下方 → confirmed。"""
    bars = completed_15m(bars5, now)
    level = _f(level)
    if level <= 0:
        return {"confirmed": False, "bar_count": 0}
    start = None
    count = 0
    last = None
    for b in bars:
        if _f(b["close"]) < level:
            if start is None:
                start = b["trade_time"]
            count += 1
            last = b["trade_time"]
            if count >= 3:
                return {"confirmed": True, "break_time": start, "bar_count": count, "last_bar_time": last,
                        "bar_close": _f(b["close"])}
        else:
            start, count, last = None, 0, None
    return {"confirmed": False, "break_time": start, "bar_count": count, "last_bar_time": last}


def detect_volume_break(bars5: List[Dict[str, Any]], level: float, now: Optional[datetime] = None,
                        ratio_threshold: float = 1.5, lookback: int = 20) -> Dict[str, Any]:
    """最近一根已完成 5min 收盘跌破 level，且量 ≥ 前 lookback 根均量 × ratio。"""
    now = now or datetime.now()
    done = []
    for b in bars5 or []:
        t = _dt(b.get("trade_time") or b.get("time"))
        if t is not None and t <= now:
            done.append(b)
    done = done[-max(lookback + 1, 2):]
    if len(done) < 3:
        return {"confirmed": False, "volume_ratio": 0.0}
    last = done[-1]
    if _f(last.get("close")) >= _f(level):
        return {"confirmed": False, "volume_ratio": 0.0}
    prev = [_f(x.get("vol")) for x in done[:-1] if _f(x.get("vol")) > 0]
    avg = (sum(prev) / len(prev)) if prev else 0.0
    vr = (_f(last.get("vol")) / avg) if avg > 0 else 0.0
    return {"confirmed": vr >= float(ratio_threshold), "volume_ratio": round(vr, 2),
            "last_bar_time": str(last.get("trade_time") or ""), "bar_close": _f(last.get("close"))}


def in_execution_block(now: Optional[datetime] = None) -> bool:
    return DW.phase_at(now).get("phase") == "intraday_blocked"


# ── PG 状态（30s 轮询去重/恢复） ─────────────────────────────────────────────
def _db():
    try:
        from app.database import SessionLocal
        return SessionLocal()
    except Exception:
        return None


def save_watch(asof: str, symbol: str, level: float, mode: str, break_time: str,
               bar_count: int = 0, last_bar_time: str = "", volume_ratio: float = 0.0,
               status: str = "watching", payload: Optional[Dict[str, Any]] = None) -> bool:
    db = _db()
    if db is None:
        return False
    try:
        from sqlalchemy import text
        db.execute(text("""
            INSERT INTO wolf_v3_intraday_watch(event_date,symbol,level,mode,break_time,bar_count,last_bar_time,volume_ratio,status,payload)
            VALUES (:d,:s,:lv,:m,:bt,:bc,:lb,:vr,:st,:pl)
            ON CONFLICT (event_date,symbol,level,mode) DO UPDATE
              SET bar_count=EXCLUDED.bar_count,last_bar_time=EXCLUDED.last_bar_time,
                  volume_ratio=EXCLUDED.volume_ratio,status=EXCLUDED.status,payload=EXCLUDED.payload,
                  updated_at=now()
        """), {"d": asof, "s": symbol, "lv": float(level), "m": mode, "bt": break_time,
               "bc": int(bar_count), "lb": last_bar_time, "vr": float(volume_ratio), "st": status,
               "pl": json.dumps(payload or {}, ensure_ascii=False)})
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass


def list_watches(asof: str, statuses: tuple = ("watching", "pending")) -> List[Dict[str, Any]]:
    db = _db()
    if db is None:
        return []
    try:
        from sqlalchemy import text
        rows = db.execute(text("""
            SELECT id,event_date,symbol,level,mode,break_time,bar_count,last_bar_time,volume_ratio,status,payload
            FROM wolf_v3_intraday_watch WHERE event_date=:d AND status = ANY(:st) ORDER BY id
        """), {"d": asof, "st": list(statuses)}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass


def mark_watch(wid: int, status: str) -> bool:
    db = _db()
    if db is None:
        return False
    try:
        from sqlalchemy import text
        db.execute(text("UPDATE wolf_v3_intraday_watch SET status=:st,updated_at=now() WHERE id=:id"),
                   {"st": status, "id": int(wid)})
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass
