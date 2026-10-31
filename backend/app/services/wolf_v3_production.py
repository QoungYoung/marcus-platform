# -*- coding: utf-8 -*-
"""wolf_v3_production.py — v3 止损层的生产接线（快照 → dsh agent → 校验 → 下单计划）。

边界：
  · 只负责“决定卖不卖、卖多少”的接线；真正的下单仍走 t_gateway.gateway_execute。
  · 默认 WOLF_V3_LIVE=0（只出计划/日志）；=1 才真正调 gateway。
  · 模型调用走生产 dsh 容器 /chat（WOLF_V3_AGENT_URL），不直连外部 API。
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from app.services import decision_window as DW
from app.services import point_engine as PE
from app.services.intraday_levels import compute_intraday_levels

_ALLOWED_STATE = {"broken", "holding", "resistance"}
_ALLOWED_MODE = {"structural_breakdown", "intraday_confirmation", "rebound_exit", "close_threshold"}


def _f(x: Any, d: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def _today8(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%Y%m%d")


def _norm_bars(daily_bars: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for b in daily_bars or []:
        d = str(b.get("trade_date") or b.get("date") or "").replace("-", "")
        if len(d) != 8:
            continue
        out.append({"trade_date": d, "open": _f(b.get("open")), "high": _f(b.get("high")),
                    "low": _f(b.get("low")), "close": _f(b.get("close")),
                    "vol": _f(b.get("vol") or b.get("amount"))})
    out.sort(key=lambda x: x["trade_date"])
    return out


def build_snapshot(symbol: str, quote: Dict[str, Any], daily_bars: List[Dict[str, Any]],
                   intraday_bars: Optional[List[Dict[str, Any]]] = None,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """PIT 快照：phase/last_price/partial OHLC + 候选；不写未来极值。"""
    now = now or datetime.now()
    asof = _today8(now)
    px = _f((quote or {}).get("current") or (quote or {}).get("last") or (quote or {}).get("close"))
    rows = _norm_bars(daily_bars)
    if not rows:
        raise ValueError("no daily bars")
    if rows[-1]["trade_date"] == asof and px > 0:
        rows[-1]["close"] = px
        rows[-1]["high"] = max(rows[-1]["high"], px)
        rows[-1]["low"] = min(rows[-1]["low"], px) if rows[-1]["low"] > 0 else px
    elif px > 0:
        rows.append({"trade_date": asof, "open": _f((quote or {}).get("open"), px),
                     "high": _f((quote or {}).get("high"), px),
                     "low": _f((quote or {}).get("low"), px), "close": px,
                     "vol": _f((quote or {}).get("vol"))})
    close = px or rows[-1]["close"]
    win = DW.phase_at(now)
    if not win.get("same_day_executable") and win.get("phase") in ("tail_preview", "intraday_blocked"):
        pass
    today_ohlc = {
        "open": _f((quote or {}).get("open"), rows[-1]["open"]),
        "high": _f((quote or {}).get("high"), rows[-1]["high"]),
        "low": _f((quote or {}).get("low"), rows[-1]["low"]),
        "last": close,
    }
    cands: List[Dict[str, Any]] = []
    try:
        _dom = "index" if str(symbol).upper() in ("000001.SH", "SH000001", "000001") else "stock"
        res = PE.compute_points(rows, asof=asof, domain=_dom, symbol=symbol)
        for p in res.get("points") or []:
            cands.append({"id": p.get("level_id") or p.get("type", "") + "_" + str(p.get("price")),
                          "type": p.get("type"), "price": round(_f(p.get("price")), 2),
                          "date": p.get("asof") or asof, "timeframe": "daily",
                          "confluence": p.get("confluence"), "state": p.get("state"),
                          "distance_pct": round((_f(p.get("price")) - close) / close * 100, 2)})
    except Exception:
        pass
    if intraday_bars:
        try:
            for p in compute_intraday_levels(intraday_bars, asof, close):
                cands.append({"id": "m15_" + p["type"] + "_" + str(p["price"]).replace(".", "_"),
                              "type": p["type"], "price": round(_f(p["price"]), 2),
                              "date": p.get("time") or asof, "timeframe": "m15",
                              "confluence": 1, "state": p.get("state"),
                              "distance_pct": round((_f(p["price"]) - close) / close * 100, 2)})
        except Exception:
            pass
    # 控制 prompt 体积：最近 40 + 结构权重最高的 20
    cands.sort(key=lambda x: abs(_f(x.get("distance_pct"))))
    near = cands[:40]
    rest = sorted([c for c in cands if c not in near],
                  key=lambda x: (-(_f(x.get("confluence"))), abs(_f(x.get("distance_pct")))))[:20]
    cands = near + rest
    return {
        "symbol": symbol, "asof": asof, "phase": win["phase"], "decision_time": win["time"],
        "data_cutoff": win["time"], "is_final": bool(win.get("is_final")),
        "last_price": round(close, 2), "today_ohlc": today_ohlc,
        "same_day_executable": bool(win.get("same_day_executable")),
        "execution_window": win.get("execution_window") or [],
        "confirmation": "close_proxy" if win["phase"] == "tail_confirm" else win["phase"],
        "data_warning": ("已收盘；当天无法成交" if win.get("is_final") else
                         "未收盘；today_low/high 只到 %s，不是全天极值" % win["time"]),
        "candidates": cands,
    }


def build_prompt(snapshot: Dict[str, Any]) -> str:
    s = dict(snapshot)
    cands = s.pop("candidates", [])
    lines = [
        "你是生产系统的点位分析器。先核对窗口，再判断动作模式，最后给触发线。",
        "只输出一个 JSON 对象，不要 markdown。",
        "硬规则：",
        "1) break_trigger 是“跌破/站不回就减仓”的线，可以在现价上方、可以 state=broken；已破要写 status_text=已跌破X。",
        "2) next_support 是现价下方 state=holding 的下一道未破支撑；没有就 null。",
        "3) risk_trigger 必须等于 break_trigger，不能拿 next_support 顶替。",
        "4) 只能用候选 id/price，不得发明价格；没把握就 no_confident_level=true。",
        "5) phase=tail_confirm 是 14:55 尾盘代理，不是 15:00 官方收盘；phase=close 当天不能成交。",
        "6) 若 action_mode=rebound_exit，必须给 rebound_zone（反抽卖出带，zone=[low,high] + candidate_ids）；否则 null。",
        "输入快照：" + json.dumps(s, ensure_ascii=False),
        "候选：" + json.dumps(cands, ensure_ascii=False),
        "输出 schema：",
        '{"action_mode":"structural_breakdown|intraday_confirmation|rebound_exit|close_threshold",'
        '"window_ok":true,"break_trigger":{"candidate_ids":["..."],"price":0.0,"state":"broken|holding|resistance",'
        '"status_text":"...","why":"..."},"next_support":{"candidate_ids":["..."],"price":0.0,"why":"..."}|null,'
        '"risk_trigger":"break_trigger","rebound_zone":{"zone":[0.0,0.0],"candidate_ids":["..."],"why":"..."}|null,'
        '"confidence":0.0,"no_confident_level":false,"uncertainty":["..."]}',
    ]
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except Exception:
        pass
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(t)):
        if t[i] == "{":
            depth += 1
        elif t[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(t[start:i + 1])
                    return obj if isinstance(obj, dict) else None
                except Exception:
                    return None
    return None


def parse_reply(reply: Any) -> Optional[Dict[str, Any]]:
    if isinstance(reply, dict):
        return reply
    if not isinstance(reply, str):
        return None
    try:
        body = json.loads(reply)
        if isinstance(body, dict) and "reply" in body:
            reply = body.get("reply")
    except Exception:
        pass
    return _extract_json(str(reply or ""))


def validate_decision(decision: Dict[str, Any], snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """校验候选 id/价格/角色，防止 agent 发明价格或拿支撑顶替。"""
    if not isinstance(decision, dict):
        return {"ok": False, "reason": "not_json"}
    mode = str(decision.get("action_mode") or "")
    if mode not in _ALLOWED_MODE:
        return {"ok": False, "reason": "bad_mode"}
    if decision.get("no_confident_level"):
        return {"ok": True, "decision": decision, "no_action": True}
    by_id = {str(c.get("id")): c for c in snapshot.get("candidates") or []}
    bt = decision.get("break_trigger")
    if not isinstance(bt, dict):
        return {"ok": False, "reason": "no_break_trigger"}
    ids = [str(x) for x in (bt.get("candidate_ids") or []) if x]
    if not ids or any(x not in by_id for x in ids):
        return {"ok": False, "reason": "unknown_candidate"}
    price = _f(bt.get("price"))
    cp = min((_f(by_id[x].get("price")) for x in ids), key=lambda p: abs(p - price))
    if price <= 0 or abs(cp - price) / max(cp, 1e-9) > 0.001:
        return {"ok": False, "reason": "price_mismatch"}
    state = str(bt.get("state") or "")
    if state not in _ALLOWED_STATE:
        return {"ok": False, "reason": "bad_state"}
    if not str(bt.get("status_text") or "").strip():
        return {"ok": False, "reason": "missing_status_text"}
    if str(decision.get("risk_trigger") or "") != "break_trigger":
        return {"ok": False, "reason": "risk_not_break_trigger"}
    rz = decision.get("rebound_zone")
    if isinstance(rz, dict):
        rz_ids = [str(x) for x in (rz.get("candidate_ids") or []) if x]
        if rz_ids and any(x not in by_id for x in rz_ids):
            return {"ok": False, "reason": "rebound_unknown_candidate"}
        zone = rz.get("zone") or []
        try:
            lo, hi = float(zone[0]), float(zone[1])
        except Exception:
            return {"ok": False, "reason": "rebound_bad_zone"}
        if lo <= 0 or hi <= 0 or lo > hi:
            return {"ok": False, "reason": "rebound_bad_zone"}
        if hi < _f(snapshot.get("last_price")) * 0.5:
            return {"ok": False, "reason": "rebound_zone_far"}
    ns = decision.get("next_support")
    if isinstance(ns, dict) and _f(ns.get("price")) > 0:
        if _f(ns.get("price")) >= _f(snapshot.get("last_price")):
            return {"ok": False, "reason": "next_support_not_below"}
    return {"ok": True, "decision": decision}


def ask_agent(snapshot: Dict[str, Any], timeout: Optional[float] = None,
              url: Optional[str] = None, session_id: Optional[str] = None) -> Dict[str, Any]:
    """调用生产 dsh 容器 /chat。失败返回 ok=False，由调用方决定不卖/降级。"""
    url = url or os.getenv("WOLF_V3_AGENT_URL") or os.getenv("PI_SERVER_URL") or "http://dsh:3001/chat"
    timeout = float(timeout or os.getenv("WOLF_V3_AGENT_TIMEOUT", "45"))
    prompt = build_prompt(snapshot)
    sid = session_id or "wolf_v3_%s_%s" % (snapshot.get("asof"), snapshot.get("symbol"))
    try:
        r = requests.post(url, json={"message": prompt, "session_id": sid},
                          headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"},
                          timeout=timeout, verify=False)
        r.raise_for_status()
        raw = r.json().get("reply", "")
    except Exception as e:
        return {"ok": False, "reason": "agent_call_failed", "error": "%s: %s" % (type(e).__name__, str(e)[:160])}
    decision = parse_reply(raw)
    if not decision:
        return {"ok": False, "reason": "agent_bad_json", "raw": str(raw)[:500]}
    v = validate_decision(decision, snapshot)
    v["raw_reply"] = str(raw)[:2000]
    return v


def _cache_path() -> str:
    d = os.getenv("DATA_DIR", "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "wolf_v3_agent_cache.json")


def _load_cache() -> Dict[str, Any]:
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _save_cache(c: Dict[str, Any]) -> None:
    try:
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
        os.replace(tmp, _cache_path())
    except Exception:
        pass


def _cache_key(snapshot: Dict[str, Any]) -> str:
    return "%s|%s" % (snapshot.get("asof"), snapshot.get("symbol"))


def precompute_agent(snapshot: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    """14:50 预计算：结果落盘，14:55 直接读，避免 5 分钟内现算。"""
    res = ask_agent(snapshot, timeout=timeout)
    c = _load_cache()
    c[_cache_key(snapshot)] = {"ts": time.time(), "phase": snapshot.get("phase"),
                               "last_price": snapshot.get("last_price"), "result": res}
    _save_cache(c)
    return res


def ask_agent_cached(snapshot: Dict[str, Any], timeout: Optional[float] = None,
                     ttl: Optional[float] = None) -> Dict[str, Any]:
    """优先读 14:50 缓存；过期/无缓存才现场调。"""
    ttl = float(ttl if ttl is not None else os.getenv("WOLF_V3_PRECOMPUTE_TTL", "900"))
    c = _load_cache().get(_cache_key(snapshot)) or {}
    if c and (time.time() - float(c.get("ts") or 0)) <= ttl:
        r = c.get("result") or {}
        if r.get("ok"):
            return r
    return ask_agent(snapshot, timeout=timeout)


def save_rebound_watch(symbol: str, asof: str, zone_low: float, zone_high: float,
                       break_price: float = 0.0, target_pct: float = 0.5,
                       source: str = "v3_agent", payload: Optional[Dict[str, Any]] = None,
                       invalidation_level: Optional[float] = None) -> bool:
    db = _db()
    if db is None:
        return False
    try:
        from sqlalchemy import text
        db.execute(text("""
            INSERT INTO wolf_v3_rebound_watch(event_date,symbol,zone_low,zone_high,break_price,target_pct,invalidation_level,status,source,payload)
            VALUES (:d,:s,:lo,:hi,:bp,:tp,:inv,'watching',:src,:pl)
            ON CONFLICT (event_date,symbol,zone_low,zone_high) DO UPDATE
              SET updated_at=now(), payload=EXCLUDED.payload, invalidation_level=EXCLUDED.invalidation_level
        """), {"d": asof, "s": symbol, "lo": float(zone_low), "hi": float(zone_high),
               "bp": float(break_price or 0), "tp": float(target_pct),
               "inv": float(invalidation_level if invalidation_level is not None else zone_high),
               "src": source, "pl": json.dumps(payload or {}, ensure_ascii=False)})
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


def list_rebound_watches(asof: str, statuses: tuple = ("watching", "pending")) -> List[Dict[str, Any]]:
    db = _db()
    if db is None:
        return []
    try:
        from sqlalchemy import text
        rows = db.execute(text("""
            SELECT id,event_date,symbol,zone_low,zone_high,break_price,target_pct,invalidation_level,status,source,payload
            FROM wolf_v3_rebound_watch
            WHERE event_date=:d AND status = ANY(:st)
            ORDER BY id
        """), {"d": asof, "st": list(statuses)}).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        try:
            db.close()
        except Exception:
            pass


def mark_rebound_watch(wid: int, status: str) -> bool:
    db = _db()
    if db is None:
        return False
    try:
        from sqlalchemy import text
        db.execute(text("UPDATE wolf_v3_rebound_watch SET status=:st,updated_at=now() WHERE id=:id"),
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


def rebound_zone_from_level(level: float, candidates: List[Dict[str, Any]],
                            max_pct: float = 0.01) -> Dict[str, float]:
    """3×15 确认后：以跌破位为下沿，上方最近结构位/1% 为上沿。"""
    lv = float(level)
    if lv <= 0:
        return {}
    above = []
    for c in candidates or []:
        p = _f(c.get("price"))
        if p > lv * 1.0001:
            above.append(p)
    hi = min(above) if above else lv * (1.0 + max_pct)
    hi = min(hi, lv * (1.0 + max_pct))
    return {"zone_low": round(lv, 4), "zone_high": round(hi, 4)}


def in_rebound_zone(price: float, lo: float, hi: float) -> bool:
    return float(lo) <= float(price) <= float(hi)


def rebound_signal(price: float, high: float, lo: float, hi: float) -> Dict[str, bool]:
    """现价在带内 / 盘中冲高触带但现价回落（可按 zone 下沿限价）。"""
    px, hp = float(price), float(high)
    return {"in_zone": in_rebound_zone(px, lo, hi),
            "touch_limit": (not in_rebound_zone(px, lo, hi)) and (float(lo) <= hp <= float(hi)) and px < float(lo)}


def plan_sell(symbol: str, quote: Dict[str, Any], target_pct: float,
              account_id: str = "stock", allow_floor: bool = True) -> Dict[str, Any]:
    """按 target_pct（目标剩余仓位）算卖出量；只卖可卖，尊重底仓 floor。"""
    from app.services.t_gateway import get_sellable_ledger, base_floor_shares
    px = _f((quote or {}).get("current") or (quote or {}).get("last"))
    if px <= 0:
        return {"ok": False, "reason": "no_price"}
    item = (get_sellable_ledger(account_id=account_id).get(symbol) or {})
    sellable = int(item.get("sellable") or 0)
    if sellable < 100:
        return {"ok": False, "reason": "no_sellable"}
    floor = int(base_floor_shares(account_id, symbol, volume=sellable) or 0)
    # 止损/破位是保护性卖出：不能用“底仓 floor”挡住止血（网关 is_stop_loss=True 同样豁免 floor）
    cap = sellable if allow_floor else max(sellable - floor, 0)
    pct = max(min(float(target_pct), 1.0), 0.0)
    raw = sellable if pct <= 0 else max(sellable - int(sellable * pct), 0)
    vol = min(raw, cap) if pct > 0 else sellable
    vol = (vol // 100) * 100
    if vol < 100:
        return {"ok": False, "reason": "floor_blocks", "sellable": sellable, "floor": floor}
    return {"ok": True, "symbol": symbol, "price": px, "volume": vol,
            "sellable": sellable, "floor": floor, "target_pct": pct, "account_id": account_id}


def _actions_path() -> str:
    d = os.getenv("DATA_DIR", "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "wolf_v3_actions.jsonl")


def action_key(asof: str, symbol: str, action: str, level: Any) -> str:
    return "%s|%s|%s|%s" % (asof, symbol, action, level)


def _db():
    try:
        from app.database import SessionLocal
        return SessionLocal()
    except Exception:
        return None


def pg_action_exists(key: str) -> Optional[bool]:
    db = _db()
    if db is None:
        return None
    try:
        from sqlalchemy import text
        row = db.execute(text("SELECT 1 FROM wolf_v3_actions WHERE action_key=:k LIMIT 1"),
                         {"k": key}).fetchone()
        return row is not None
    except Exception:
        return None
    finally:
        try:
            db.close()
        except Exception:
            pass


def pg_record_action(rec: Dict[str, Any]) -> bool:
    db = _db()
    if db is None:
        return False
    try:
        from sqlalchemy import text
        db.execute(text("""
            INSERT INTO wolf_v3_actions(action_key,event_date,symbol,action,level_price,target_pct,payload)
            VALUES (:k,:d,:s,:a,:p,:t,:j)
            ON CONFLICT (action_key) DO NOTHING
        """), {"k": rec.get("key"), "d": rec.get("date"), "s": rec.get("symbol"),
               "a": rec.get("action"), "p": _f((rec.get("level") or {}).get("price")),
               "t": rec.get("target_pct"), "j": json.dumps(rec, ensure_ascii=False)})
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


def pg_symbol_action_today(symbol: str, asof: str) -> bool:
    db = _db()
    if db is None:
        return False
    try:
        from sqlalchemy import text
        row = db.execute(text("SELECT 1 FROM wolf_v3_actions WHERE event_date=:d AND symbol=:s LIMIT 1"),
                         {"d": asof, "s": symbol}).fetchone()
        return row is not None
    except Exception:
        return False
    finally:
        try:
            db.close()
        except Exception:
            pass


def pg_daily_counts(asof: str) -> Dict[str, int]:
    db = _db()
    if db is None:
        return {"total": 0, "symbol": 0}
    try:
        from sqlalchemy import text
        total = db.execute(text("SELECT count(*) FROM wolf_v3_actions WHERE event_date=:d"),
                           {"d": asof}).scalar() or 0
        return {"total": int(total)}
    except Exception:
        return {"total": 0}
    finally:
        try:
            db.close()
        except Exception:
            pass


def already_done(key: str) -> bool:
    pg = pg_action_exists(key)
    if pg is True:
        return True
    p = _actions_path()
    try:
        if not os.path.exists(p):
            return False
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    if json.loads(line).get("key") == key:
                        return True
                except Exception:
                    continue
    except Exception:
        pass
    return False


def record_action(rec: Dict[str, Any]) -> None:
    pg_record_action(rec)
    try:
        with open(_actions_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def execute_plan(plan: Dict[str, Any], reason: str, live: Optional[bool] = None) -> Dict[str, Any]:
    """执行卖出计划；默认只返回计划，不真下单。"""
    if not plan.get("ok"):
        return {"status": "skipped", "reason": plan.get("reason")}
    live = (os.getenv("WOLF_V3_LIVE", "0").strip() in ("1", "true", "yes", "on")) if live is None else bool(live)
    if not live:
        return {"status": "planned", "volume": plan["volume"], "price": plan["price"]}
    # 单日笔数上限（PG 权威；无 DB 时放行）
    try:
        max_daily = int(os.getenv("WOLF_V3_MAX_DAILY_SELLS", "3"))
        cnt = int(pg_daily_counts(plan.get("asof") or datetime.now().strftime("%Y%m%d")).get("total", 0))
        if max_daily > 0 and cnt >= max_daily:
            return {"status": "blocked", "reason": "daily_sell_cap"}
    except Exception:
        pass
    # 单票单次上限：默认不超过可卖的 60%（target_pct 已表达目标仓位）
    try:
        max_pct = float(os.getenv("WOLF_V3_MAX_SYMBOL_PCT", "0.60"))
        if plan.get("sellable") and plan["volume"] > int(plan["sellable"] * max_pct):
            plan["volume"] = (int(plan["sellable"] * max_pct) // 100) * 100
            if plan["volume"] < 100:
                return {"status": "blocked", "reason": "symbol_cap"}
    except Exception:
        pass
    from app.services.t_gateway import gateway_execute
    gw = gateway_execute(plan["symbol"], "sell", plan["price"], plan["volume"],
                         reason=reason, decision_source="agent", is_stop_loss=True,
                         kind="wolf_v3_stop", account_id=plan.get("account_id") or os.getenv("T_MONITOR_ACCOUNT", "stock"))
    return gw
