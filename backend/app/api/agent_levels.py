# -*- coding: utf-8 -*-
"""Agent 关键位/状态 API（Portfolio 页替换旧防御系统）。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from sqlalchemy import text

from app.database import SessionLocal
from app.services import point_engine as PE

router = APIRouter(prefix="/portfolio", tags=["Portfolio"])

_INDEX_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None, "points": [], "bars": [], "quote": {}}
_INDEX_TTL = 60.0
_INDEX_AGENT_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_INDEX_AGENT_TTL = 300.0


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


def _index_levels() -> Dict[str, Any]:
    """上证当日上下关键位（无持仓也返回；60s 缓存）。"""
    import time as _t
    now = _t.time()
    if _INDEX_CACHE.get("data") and now - float(_INDEX_CACHE.get("ts") or 0) < _INDEX_TTL:
        return _INDEX_CACHE["data"]  # type: ignore[return-value]
    out: Dict[str, Any] = {"symbol": "000001.SH", "name": "上证指数", "current": None,
                           "high": None, "low": None, "supports": [], "resistances": [],
                           "asof": datetime.now().strftime("%Y%m%d"), "source": "point_engine",
                           "stale": False}
    try:
        from app.services.t_data_sources import fetch_quote_one
        q = fetch_quote_one("SH000001") or {}
        cur = float(q.get("current") or 0)
        out.update({"current": cur or None, "high": q.get("high"), "low": q.get("low"),
                    "quote_time": q.get("quote_dt")})
        bars: List[Dict[str, Any]] = []
        try:
            import os as _os
            from pathlib import Path as _P
            _root = _P(__file__).resolve().parents[2]
            _cands = [_os.path.join(_os.getenv("DATA_DIR", ""), "index_daily_000001.json")
                      if _os.getenv("DATA_DIR") else "",
                      str(_root / "data" / "index_daily_000001.json"),
                      "/app/data/index_daily_000001.json"]
            _idx_path = next((x for x in _cands if x and _os.path.exists(x)), "")
            raw = json.load(open(_idx_path, encoding="utf-8"))
            for r in raw:
                try:
                    d = str(r.get("trade_date") or "").replace("-", "")
                    if len(d) == 8 and d <= out["asof"]:
                        bars.append({"trade_date": d, "open": float(r.get("open") or 0),
                                     "high": float(r.get("high") or 0), "low": float(r.get("low") or 0),
                                     "close": float(r.get("close") or 0), "vol": float(r.get("vol") or 0)})
                except Exception:
                    continue
        except Exception:
            bars = []
        bars.sort(key=lambda x: x["trade_date"])
        # 补 9/1 之后的数据（Tushare 中继，失败则用已有 bars + 当日 quote）
        if bars:
            last = bars[-1]["trade_date"]
            if last < out["asof"]:
                try:
                    import sys as _sys
                    _sys.path.insert(0, str(_P(__file__).resolve().parents[2]))
                    from core.tushare_relay import relay_query
                    df = relay_query("index_daily", ts_code="000001.SH",
                                     start_date=last, end_date=out["asof"])
                    if df is not None and len(df) > 0:
                        for _, r in df.sort_values("trade_date").iterrows():
                            try:
                                d = str(r["trade_date"])
                                if d > last and d <= out["asof"]:
                                    bars.append({"trade_date": d, "open": float(r["open"]),
                                                 "high": float(r["high"]), "low": float(r["low"]),
                                                 "close": float(r["close"]), "vol": float(r.get("vol") or 0)})
                            except Exception:
                                continue
                except Exception as _re:
                    out["stale"] = True
                    out["relay_error"] = "%s: %s" % (type(_re).__name__, str(_re)[:120])
        if cur > 0 and (not bars or bars[-1]["trade_date"] < out["asof"]):
            bars.append({"trade_date": out["asof"], "open": float(q.get("open") or cur),
                         "high": float(q.get("high") or cur), "low": float(q.get("low") or cur),
                         "close": cur, "vol": float(q.get("vol") or 0)})
        if bars and cur > 0:
            res = PE.compute_points(bars, asof=out["asof"], domain="index", symbol="000001.SH")
            pts = res.get("points") or []
            def _row(p: Dict[str, Any]) -> Dict[str, Any]:
                return {"price": round(float(p.get("price") or 0), 2), "type": p.get("type"),
                        "state": p.get("state"), "confluence": p.get("confluence"),
                        "distance_pct": p.get("distance_pct")}
            sup = [_row(p) for p in pts if float(p.get("price") or 0) < cur]
            resi = [_row(p) for p in pts if float(p.get("price") or 0) > cur]
            sup = sorted({round(r["price"], 2): r for r in sup}.values(), key=lambda x: -x["price"])[:4]
            resi = sorted({round(r["price"], 2): r for r in resi}.values(), key=lambda x: x["price"])[:4]
            out.update({"supports": sup, "resistances": resi,
                        "asof_bar": bars[-1]["trade_date"], "bars": len(bars)})
            _INDEX_CACHE["points"] = pts
            _INDEX_CACHE["bars"] = bars
            _INDEX_CACHE["quote"] = q
    except Exception as e:
        out["error"] = str(e)[:120]
    _INDEX_CACHE.update({"ts": now, "data": out})
    return out


def _index_agent() -> Dict[str, Any]:
    """让 dsh agent 单独给上证上下关键位（60s 缓存；失败返回 ok=false）。"""
    import time as _t
    now = _t.time()
    if _INDEX_AGENT_CACHE.get("data") and now - float(_INDEX_AGENT_CACHE.get("ts") or 0) < _INDEX_AGENT_TTL:
        return _INDEX_AGENT_CACHE["data"]  # type: ignore[return-value]
    out: Dict[str, Any] = {"ok": False, "supports": [], "resistances": [],
                           "current_state": "", "risk_trigger": None,
                           "confidence": None, "error": ""}
    try:
        import os, json as _j, re as _re, requests as _rq
        cur = float((_INDEX_CACHE.get("data") or {}).get("current") or 0)
        pts = _INDEX_CACHE.get("points") or []
        if cur <= 0 or not pts:
            out["error"] = "no_index_snapshot"
            _INDEX_AGENT_CACHE.update({"ts": now, "data": out})
            return out
        cands = []
        for p in pts:
            try:
                cands.append({"type": p.get("type"), "price": round(float(p.get("price") or 0), 2),
                              "state": p.get("state"), "confluence": p.get("confluence"),
                              "distance_pct": p.get("distance_pct")})
            except Exception:
                continue
        cands = sorted(cands, key=lambda x: abs(float(x.get("distance_pct") or 0)))[:50]
        prompt = (
            "你是上证指数关键位分析器。只输出一个 JSON，不要 markdown。\n"
            "当前上证：%.2f；今日高 %.2f、低 %.2f。\n"
            "请给：上方压力 2-4 档、下方支撑 2-4 档、当前状态一句话、最关键的破位/防守线。\n"
            "只能用候选里的价格，不得发明。\n候选：%s\n"
            '输出：{"current_state":"...","supports":[{"price":0.0,"type":"...","why":"..."}],'
            '"resistances":[{"price":0.0,"type":"...","why":"..."}],'
            '"risk_trigger":{"price":0.0,"type":"...","why":"..."},'
            '"confidence":0.0,"no_confident_level":false}'
        ) % (cur, float((_INDEX_CACHE.get("data") or {}).get("high") or 0),
             float((_INDEX_CACHE.get("data") or {}).get("low") or 0), _j.dumps(cands, ensure_ascii=False))
        url = os.getenv("WOLF_V3_AGENT_URL") or os.getenv("PI_SERVER_URL") or "http://dsh:3001/chat"
        r = _rq.post(url, json={"message": prompt, "session_id": "index_levels_%s" % datetime.now().strftime("%Y%m%d")},
                     headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                     timeout=float(os.getenv("WOLF_INDEX_AGENT_TIMEOUT", "20")), verify=False)
        r.raise_for_status()
        raw = r.json().get("reply", "")
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = _re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = _re.sub(r"\s*```$", "", text)
        try:
            obj = _j.loads(text)
        except Exception:
            st = text.find("{")
            obj = _j.loads(text[st:text.rfind("}") + 1]) if st >= 0 and text.rfind("}") > st else None
        if not isinstance(obj, dict):
            out["error"] = "bad_json"
        else:
            valid = {round(float(x["price"]), 2) for x in cands if x.get("price")}
            def _clean(rows):
                out_rows = []
                for x in rows or []:
                    try:
                        p = round(float(x.get("price")), 2)
                        if p in valid:
                            out_rows.append({"price": p, "type": x.get("type") or "",
                                             "why": str(x.get("why") or "")[:120]})
                    except Exception:
                        continue
                return out_rows[:4]
            out.update({"ok": True, "supports": _clean(obj.get("supports")),
                        "resistances": _clean(obj.get("resistances")),
                        "current_state": str(obj.get("current_state") or "")[:200],
                        "risk_trigger": obj.get("risk_trigger"),
                        "confidence": obj.get("confidence"),
                        "no_confident_level": bool(obj.get("no_confident_level"))})
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    _INDEX_AGENT_CACHE.update({"ts": now, "data": out})
    return out


@router.get("/agent-levels")
def agent_levels(account: str = Query("stock"), date: Optional[str] = Query(None)) -> Dict[str, Any]:
    """当前持仓的最近一次 agent 判定 + 关键位/反抽带状态。"""
    d = (date or datetime.now().strftime("%Y%m%d")).replace("-", "")
    out: Dict[str, Any] = {"date": d, "account": account, "generated_at": None,
                           "positions": [], "watches": [], "index": {},
                           "message": ""}
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
        try:
            out["index"] = _index_levels()
            out["index_agent"] = _index_agent()
        except Exception:
            out["index"] = {}
            out["index_agent"] = {}
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
