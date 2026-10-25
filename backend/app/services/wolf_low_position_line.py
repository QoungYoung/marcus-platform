# -*- coding: utf-8 -*-
"""低位超额反弹线（paper-only）：日期闸 + 方向强 + 入场日 10:00/10:30 日内 trigger。

生产约束（用户 2026-10-20）：
  · 未接真实交易接口 ⇒ 只写 paper 信号，不下单；
  · 仓位由 calc_position 决定；
  · 由统一监控器 TMonitor 每轮调用 tick()。

日期闸（基于上一交易日收盘）：
  can_do（指数非首次测试压力、有回踩、tech sideways、低位方向改善）
  + top_score >= 0.08、top1-top4 gap >= 0.05
  + 最近两日不是连续缩量普跌
个股：
  select_for_theme(line=low_position) 的 rebound_watch
  + ret5<=12%、fresh>=2%、dist_low20>=15%
  （r60 默认关：`WOLF_LOWPOS_R60=0`，改由入场日盘口 D 过滤）
入场日：
  10:00 或 10:30 报价：gap>=-2%、当日低点>=-3%、现价>=昨收、现价>=当日均价(VWAP)
"""
from __future__ import annotations
import datetime as dt
import json, os, sys, time
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "app", "services"))

def _data_root() -> str:
    """容器里 __file__ 挂载在 /app/app/services，ROOT 会算成 /；必须用 DATA_DIR 兜底。"""
    for c in (os.environ.get("DATA_DIR"), os.path.join(ROOT, "data"), "/app/data", "data"):
        if c and os.path.exists(os.path.join(c, "bars_combined.parquet")):
            return c
    return os.path.join(ROOT, "data")


DATA_ROOT = _data_root()
DATA = os.path.join(DATA_ROOT, "low_position_line")
os.makedirs(DATA, exist_ok=True)
_LAST = {"t": 0.0, "date": "", "cand": None, "done": set()}


def _on(name: str, dflt: str = "0") -> bool:
    return str(os.getenv(name, dflt) or dflt).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _on("WOLF_LOWPOS_ENABLED", "1")


def _last_trade_date(today: str) -> Optional[str]:
    try:
        import pandas as pd
        df = pd.read_parquet(os.path.join(DATA_ROOT, "bars_combined.parquet"),
                             columns=["trade_date"])
        ds = sorted(set(str(x) for x in df.trade_date.unique() if str(x) <= today))
        return ds[-1] if ds else None
    except Exception:
        return None


def _breadth_2bad(date8: str) -> bool:
    try:
        from app.services.wolf_card.can_do_gate import market_breadth
        cur = market_breadth(date8)
        # 前一交易日
        import pandas as pd
        df = pd.read_parquet(os.path.join(DATA_ROOT, "bars_combined.parquet"),
                             columns=["trade_date"])
        ds = sorted(set(str(x) for x in df.trade_date.unique() if str(x) < date8))
        prev = market_breadth(ds[-1]) if ds else {}
        bad = lambda x: (x.get("ratio") is not None and x["ratio"] < 0.6) or \
                        (x.get("median_pct") is not None and x["median_pct"] < -0.5)
        return bad(cur) and bad(prev) and (cur.get("amt5_20") or 1) < 0.95
    except Exception:
        return False


def _build_candidates(signal_date: str) -> Dict[str, Any]:
    from app.services.wolf_card.can_do_gate import can_do
    from app.services.wolf_card.selection_rules import select_for_theme

    g = can_do(signal_date, min_top_score=0.08, min_gap=0.05)
    out = {"signal_date": signal_date, "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
           "can_do": bool(g.get("can_do")), "reasons": g.get("reasons") or [],
           "top": g.get("direction_top"), "gap": g.get("direction_gap"),
           "mainline": g.get("mainline"), "mainline_r20": g.get("mainline_r20"),
           "mainline_share20": g.get("mainline_share20"),
           "mainline_sideways": g.get("mainline_sideways"),
           "dirs": [], "stocks": [], "status": "no_trade"}
    if not g.get("can_do"):
        return out
    if _breadth_2bad(signal_date):
        out["status"] = "no_trade_breadth2"
        out["reasons"] = list(out["reasons"]) + ["breadth2_deteriorating"]
        return out
    stocks = []
    for x in (g.get("low_directions") or [])[:4]:
        th = x["theme"]
        sub = "AI软" if th == "AI/算力/科技" else None
        try:
            r = select_for_theme(th, signal_date, 8, line="low_position", subtheme=sub)
        except Exception:
            continue
        codes = []
        for p in r.get("rebound_watch", []):
            c = p["code"]
            if (p.get("ret5") is not None and p["ret5"] > 0.12): continue
            if _on("WOLF_LOWPOS_R60", "0") and (p.get("r60") is not None and p["r60"] > (0.10 if th == "AI/算力/科技" else 0.15)): continue
            if (p.get("fresh_accel") is not None and p["fresh_accel"] < 0.02): continue
            if (p.get("dist_low20") is not None and p["dist_low20"] < 0.15): continue
            codes.append({"code": c, "name": p.get("name", c), "direction": th,
                          "pct_chg": p.get("pct_chg"), "ret5": p.get("ret5"),
                          "r60": p.get("r60"), "fresh": p.get("fresh_accel"),
                          "dist_low20": p.get("dist_low20")})
        if codes:
            out["dirs"].append(th)
            stocks += codes[:3]
    # 去重
    seen = set(); uniq = []
    for s in stocks:
        if s["code"] in seen: continue
        seen.add(s["code"]); uniq.append(s)
    out["stocks"] = uniq
    out["status"] = "ready" if len(uniq) >= 2 else "no_trade_too_few"
    return out


def _load_candidates(today: str) -> Dict[str, Any]:
    # 数据更新后必须重算：不能只看 today，否则 10-20 补数后仍用 10-19 的旧候选
    last = _last_trade_date(today)
    if not last:
        return {"status": "no_data", "stocks": []}
    signal_date = last
    if signal_date == today:
        import pandas as pd
        df = pd.read_parquet(os.path.join(DATA_ROOT, "bars_combined.parquet"), columns=["trade_date"])
        ds = sorted(set(str(x) for x in df.trade_date.unique() if str(x) < today))
        signal_date = ds[-1] if ds else signal_date
    if _LAST.get("cand") is not None and _LAST.get("date") == today and \
            _LAST["cand"].get("signal_date") == signal_date:
        return _LAST["cand"]
    p = os.path.join(DATA, "candidates_%s.json" % today)
    if os.path.exists(p):
        try:
            c = json.load(open(p, encoding="utf-8"))
            if c.get("signal_date") != signal_date:
                c = _build_candidates(signal_date)
                json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            c = _build_candidates(signal_date)
    else:
        c = _build_candidates(signal_date)
        try:
            json.dump(c, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
    _LAST.update({"date": today, "cand": c})
    return c


def _position_for(code: str) -> Dict[str, Any]:
    """仓位由 calc_position 决定；失败回退 5% 试探仓。"""
    try:
        from app.api.indicator import calc_position
        from app.models.indicator import CalcPositionRequest
        req = CalcPositionRequest(symbol=code, signal_strength="medium", chain_role="mid",
                                  tier="probe", stance="yellow", intent="new_base",
                                  account_id="stock", mainline_dir=False)
        r = calc_position(req)
        pct = float(getattr(r, "probe_pct", 0) or getattr(r, "rec_pct", 0) or 0)
        return {"pct": pct, "shares": int(getattr(r, "probe_shares", 0) or 0),
                "source": "calc_position"}
    except Exception as e:
        return {"pct": 5.0, "shares": 0, "source": "fallback_5pct", "error": str(e)[:120]}


def _trigger(quote: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        cur = float(quote.get("current") or 0); pre = float(quote.get("pre_close") or 0)
        op = float(quote.get("open") or 0); low = float(quote.get("low") or 0)
        avg = float(quote.get("average") or 0)
    except Exception:
        return None
    if min(cur, pre, op, low, avg) <= 0:
        return None
    gap = op / pre - 1; lowr = low / pre - 1; p = cur / pre - 1; v = cur / avg - 1
    ok = gap >= -0.02 and lowr >= -0.03 and p >= 0 and v >= 0
    return {"ok": bool(ok), "gap": round(gap, 4), "low": round(lowr, 4),
            "pct": round(p, 4), "vwap": round(v, 4)}


def tick() -> None:
    if not enabled():
        return
    now = dt.datetime.now()
    if now.weekday() >= 5:
        return
    hm = now.strftime("%H:%M")
    if hm < "09:30" or hm > "11:30" and hm < "13:00" or hm > "15:00":
        return
    today = now.strftime("%Y%m%d")
    c = _load_candidates(today)
    if c.get("status") != "ready":
        return
    # 只在 10:00-10:05 / 10:30-10:35 触发
    in_win = ("10:00" <= hm <= "10:05") or ("10:30" <= hm <= "10:35")
    if not in_win:
        return
    if time.time() - _LAST.get("t", 0) < 20:
        return
    _LAST["t"] = time.time()
    from app.services.t_data_sources import fetch_quote_one
    for s in c.get("stocks", []):
        code = s["code"]
        if (today, code) in _LAST["done"]:
            continue
        try:
            q = fetch_quote_one(code) or {}
        except Exception:
            continue
        tr = _trigger(q)
        if not tr or not tr.get("ok"):
            continue
        pos = _position_for(code)
        rec = {"ts": now.isoformat(timespec="seconds"), "date": today, "signal_date": c.get("signal_date"),
               "code": code, "name": s.get("name"), "direction": s.get("direction"),
               "quote": {k: q.get(k) for k in ("current", "pre_close", "open", "low", "average", "change_pct")},
               "trigger": tr, "position": pos, "status": "paper"}
        p = os.path.join(DATA, "signals.jsonl")
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        try:
            json.dump(rec, open(os.path.join(DATA, "latest.json"), "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass
        _LAST["done"].add((today, code))
        print("[LowPos] 纸面信号 %s %s %.2f%% pos=%.1f%%" % (code, s.get("name"), tr["pct"] * 100, pos.get("pct", 0)))
        if _on("WOLF_LOWPOS_NOTIFY", "0"):
            try:
                from app.services.alert_hub import push_qq
                push_qq("【低位超额线·纸面】%s %s 触发 %.2f%% 仓位%.1f%%" % (
                    code, s.get("name", ""), tr["pct"] * 100, pos.get("pct", 0)))
            except Exception:
                pass


def status() -> Dict[str, Any]:
    return {"enabled": enabled(), "last": _LAST.get("date"),
            "candidates": _LAST.get("cand") or {}, "done": list(_LAST.get("done") or [])}


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=1))
