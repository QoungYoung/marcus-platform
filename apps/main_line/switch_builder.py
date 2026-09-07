# -*- coding: utf-8 -*-
"""switch_builder.py — 狼大式切换卖旧建新·DRY 评估 (2026-09-07, openspec add-wolf-trial-ladder)

- sell_old: 持仓方向掉出 fusion TOP3 且 stage∈{下跌中,证伪} → 布卖腿(quote.vwap_break, publisher=switch)
- keep   : 掉出但仍强/筑底 → 留
- buy_new: fusion TOP1∪TOP2 内 stock_confirm 突破候选/确认活跃股 → 布 253(custom_m5dump)+254(custom_prevlow) 低吸腿
SWITCH_AUTO_EXEC=1 → 直接布腿到 t_conditions(TMonitor 低吸/卖腿触发执行); 默认 DRY 只写清单。
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/app/jobs")   # rotation_switch_arm(生产)
DATA = os.environ.get("DATA_DIR", "/app/data")
PLAN_FILE = os.path.join(DATA, "switch_builder_plan.json")
WEAK_STAGES = ("下跌中", "证伪")
HOLD_STAGES = ("缩量止跌", "结构到位", "突破候选", "确认")


def _load(name):
    try:
        with open(os.path.join(DATA, name), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def fusion_top3():
    ml = _load("main_line_state.json")
    fus = ml.get("fusion") or {}
    rank = sorted(fus.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))
    return [k for k, _ in rank[:3]]


def held_positions():
    import psycopg2
    try:
        conn = psycopg2.connect(os.environ["DATABASE_URL"]); cur = conn.cursor()
        cur.execute("SELECT symbol, volume FROM paper_positions WHERE account_id='stock' AND volume>0")
        out = [{"symbol": str(r[0]), "volume": int(r[1] or 0)} for r in cur.fetchall()]
        cur.close(); conn.close()
        return out
    except Exception:
        return []


def stock_stage(code6):
    sc = _load("stock_confirm_result.json")
    for v in sc.values():
        if not isinstance(v, dict):
            continue
        for s in (v.get("stocks") or []):
            if str(s.get("code", "")).split(".")[0] == code6:
                return str(s.get("stage") or "")
    return ""


def active_stocks_by(stages):
    ml = _load("main_line_state.json"); fus = ml.get("fusion") or {}
    top12 = [k for k, _ in sorted(fus.items(), key=lambda kv: -(kv[1].get("score", 0) or 0))[:2]]
    sc = _load("stock_confirm_result.json")
    out = {}
    for cname, v in sc.items():
        if not isinstance(v, dict) or v.get("theme") not in top12:
            continue
        for s in (v.get("stocks") or []):
            st = str(s.get("stage") or "")
            c6 = str(s.get("code", "")).split(".")[0]
            if st in stages:
                out.setdefault(c6, (v.get("theme"), st))
    return out, top12


def symbol_themes_of(symbol):
    try:
        from sector_g3 import symbol_themes
        return symbol_themes(symbol)
    except Exception:
        return []


def _arm_legs(plan):
    """SWITCH_AUTO_EXEC=1: 卖旧→custom(vwap_break)卖腿; 买新→253/254 低吸腿(t_conditions, publisher=switch)"""
    import psycopg2
    from datetime import datetime
    try:
        from rotation_switch_arm import expire_old, arm, SELL_EXPR, BUY_253_EXPR, BUY_254_EXPR
    except Exception:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "jobs"))
            from rotation_switch_arm import expire_old, arm, SELL_EXPR, BUY_253_EXPR, BUY_254_EXPR
        except Exception as ex:
            return [{"err": str(ex)[:120]}]
    conn = psycopg2.connect(os.environ["DATABASE_URL"]); conn.autocommit = True
    cur = conn.cursor(); today = datetime.now().strftime("%Y%m%d")
    expire_old(cur, today)
    armed = []
    for s in plan.get("sell_old") or []:
        rid = arm(conn, cur, s["symbol"], "custom", "sell", SELL_EXPR, today)
        armed.append({"type": "sell_vwap_break", "symbol": s["symbol"], "id": rid})
    for b in plan.get("buy_new") or []:
        code = b["code"]
        sym = ("SH" if str(code)[0] == "6" else "SZ") + str(code)
        rid1 = arm(conn, cur, sym, "custom_m5dump", "buy", BUY_253_EXPR, today)
        rid2 = arm(conn, cur, sym, "custom_prevlow", "buy", BUY_254_EXPR, today)
        armed.append({"type": "buy_253/254", "symbol": sym, "253": rid1, "254": rid2})
    cur.close(); conn.close()
    return armed


def build_plan():
    top3 = fusion_top3()
    tiers = {}
    sell_old, keep = [], []
    for p in held_positions():
        sym = p["symbol"]; c6 = "".join(ch for ch in sym if ch.isdigit())[:6]
        ths = symbol_themes_of(sym)
        stage = stock_stage(c6)
        try:
            from tranche_ladder import tier_for
            tier, why = tier_for(sym)
        except Exception:
            tier, why = "?", "err"
        tiers[sym] = {"tier": tier, "themes": ths, "stage": stage, "why": why}
        dropped = not (set(ths) & set(top3))
        if dropped:
            if stage in WEAK_STAGES:
                sell_old.append({"symbol": sym, "stage": stage, "themes": ths})
            elif stage in HOLD_STAGES or tier in ("ambush", "trial", "normal"):
                keep.append({"symbol": sym, "stage": stage, "themes": ths})
            else:
                keep.append({"symbol": sym, "stage": stage or "unknown(留观察)", "themes": ths})
        else:
            keep.append({"symbol": sym, "stage": stage or "主线内", "themes": ths})
    buy_new, _ = active_stocks_by(("突破候选", "确认"))
    try:
        from tranche_ladder import escalate_signal, sync_normal_upgrade
        esc, trig, upgraded = sync_normal_upgrade()
    except Exception:
        esc, trig, upgraded = False, [], []
    auto_exec = os.getenv("SWITCH_AUTO_EXEC", "0") == "1"
    plan = {"top3": top3,
            "sell_old": sell_old, "keep": keep,
            "buy_new": [{"code": k, "theme": v[0], "stage": v[1]} for k, v in buy_new.items()],
            "tiers": tiers,
            "escalate": {"ok": esc, "triggers": trig, "upgraded": upgraded},
            "auto_exec": auto_exec, "dry": not auto_exec, "armed": []}
    if auto_exec:
        plan["armed"] = _arm_legs(plan)
    try:
        with open(PLAN_FILE, "w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return plan


if __name__ == "__main__":
    plan = build_plan()
    print("TOP3:", plan["top3"])
    print("sell_old:", plan["sell_old"])
    print("keep:", plan["keep"])
    print("buy_new:", plan["buy_new"])
    print("escalate:", plan["escalate"])
    print("armed:", plan["armed"])
    print("WROTE", PLAN_FILE)
