# -*- coding: utf-8 -*-
"""rotation_switch_arm.py — 主线内切换·动态布腿器（决策→TMonitor腿，非直接下单）

链：决策(规则/信号层，等价 auto_trade Pi 上下文) → 布腿到 t_conditions(account=stock)
  → TMonitor 30s 轮询触发卖旧/买新（卖=quote.vwap_break；买=index.m5_dump 253 / dip_prev_low 254）

动作：
  1) expire 昨日 publisher='rotation_switch' 的旧腿
  2) 卖侧：当前持仓落在 拥挤无空间/出货链 → 布 vwap_break 卖腿
  3) 买侧：room/holdT 且(主线 或 非build+健康 的防御/资源二线) → 选 LOW/MID 非拥挤 ≤3 只，
     每只布 m5dump(253) + dip_prev_low(254) 两条买腿
安全：本脚本只布腿不直接下单；执行由 TMonitor/t_gateway 完成。
用法: python -u jobs/rotation_switch_arm.py  （SWITCH_ARM_DRY=1 只输出不写库）
"""
import os, sys, json, time
sys.path.insert(0, "/app/app")
sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "data")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")

def load(name):
    try:
        return json.load(open(os.path.join(DATA, name), encoding="utf-8"))
    except Exception:
        return {}

def norm(s):
    return str(s).replace(" ", "").replace("　", "")

def _today():
    from datetime import date
    return date.today().isoformat()

def held_positions():
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT symbol, volume FROM paper_positions WHERE account_id='stock' AND volume>0")
        out = [{"symbol": str(r[0]), "volume": int(r[1] or 0)} for r in cur.fetchall()]
        cur.close(); conn.close(); return out
    except Exception:
        return []

def concepts_map():
    out = {}
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT ts_code, concept_name FROM stock_concept_map")
        for ts, cn in cur.fetchall():
            out.setdefault(str(ts), []).append(str(cn))
        cur.close(); conn.close()
    except Exception:
        pass
    return out

def chains_of(sym, cm):
    from rotation_universe import SUB_UNIVERSE as SUB
    ts = sym[2:] + "." + sym[:2]
    names = cm.get(sym, []) + cm.get(ts, [])
    return [sub for sub, kws in SUB.items() if any(norm(k) in norm(n) for n in names for k in kws)]

def bad_set():
    bad = set()
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT ts_code FROM stock_pool WHERE is_st=1 OR name LIKE 'ST%' OR name LIKE '*ST%'")
        bad |= {str(r[0]) for r in cur.fetchall()}
        cur.execute("SELECT symbol FROM risk_flags WHERE flag_type='is_st' AND value='1' OR flag_type='earnings_bad'")
        bad |= {str(r[0]) for r in cur.fetchall()}
        cur.close(); conn.close()
    except Exception:
        pass
    return bad

def pick_buy(chain, exclude, limit=3):
    from rotation_universe import SUB_UNIVERSE as SUB
    kws = SUB.get(chain) or []
    if not kws:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT concept_name, ts_code FROM stock_concept_map")
        cm = {}
        for cn, ts in cur.fetchall():
            cm.setdefault(str(ts), []).append(str(cn))
        cur.close(); conn.close()
    except Exception:
        return []
    bad = bad_set()
    bl = load("crowding_blacklist.json") or {}
    detail = bl.get("symbols_detail") or {}
    cands = []
    for ts, names in cm.items():
        if any(norm(k) in norm(n) for n in names for k in kws):
            xq = "SH" + ts[:6] if ts.endswith(".SH") else ("SZ" + ts[:6] if ts.endswith(".SZ") else ts)
            if xq not in exclude and ts not in detail and ts not in bad:
                cands.append((ts, xq))
    out = []
    for ts, xq in cands[:80]:
        if len(out) >= limit:
            break
        try:
            import pandas as pd
            import position_class as pc
            body = {"api_name": "daily", "token": os.getenv("TUSHARE_TOKEN", ""),
                    "params": {"ts_code": ts, "start_date": "20250101", "end_date": "20260901"},
                    "fields": "ts_code,trade_date,close"}
            import urllib.request
            req = urllib.request.Request(os.getenv("TUSHARE_API_URL", ""), data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
            raw = urllib.request.urlopen(req, timeout=30).read()
            d = json.loads(raw.decode())
            rows = d.get("data", {}).get("items") or []
            if len(rows) < 60:
                time.sleep(0.1); continue
            ser = pd.Series([float(x[2]) for x in rows], index=pd.to_datetime([str(x[1]) for x in rows], format="%Y%m%d"))
            f = pc.position_features(ser)
            pos = pc.classify(f)["position"] if f else None
            if pos in ("LOW", "MID"):
                out.append({"symbol": xq, "ts_code": ts, "position": pos})
        except Exception:
            pass
        time.sleep(0.1)
    return out

SELL_EXPR = {"op": "==", "field": "quote.vwap_break", "value": True}
BUY_253_EXPR = {"and": [{"op": ">=", "field": "index.m5_dump", "value": 0.4},
                        {"op": ">", "field": "quote.average", "value": 0},
                        {"op": ">", "field": "quote.current", "value": 0}]}
BUY_254_EXPR = {"and": [{"op": "==", "field": "quote.dip_prev_low", "value": True},
                        {"op": ">", "field": "vol_ratio", "value": 0},
                        {"op": "<=", "field": "vol_ratio", "value": 0.7},
                        {"op": ">", "field": "quote.average", "value": 0},
                        {"op": ">", "field": "quote.current", "value": 0}]}

def expire_old(cur, today):
    cur.execute("UPDATE t_conditions SET status='expired' WHERE account_id='stock' AND publisher='rotation_switch' AND status='active' AND trade_date < %s", (today,))

def arm(db, cur, symbol, trigger_kind, direction, expr, trade_date):
    from app.services import t_db
    return t_db.upsert_condition({"account_id": "stock", "symbol": symbol, "trade_date": trade_date,
                                  "trigger_kind": trigger_kind, "direction": direction,
                                  "expression": expr, "status": "active", "armed": 1,
                                  "publisher": "rotation_switch"})

def main():
    dry = os.getenv("SWITCH_ARM_DRY", "0").strip() in ("1", "true", "yes")
    wave = load("wave_state.json"); ru = load("rotation_universe_result.json")
    ml = load("main_line_state.json")
    wop = str(wave.get("operation") or "side").lower()
    crowded = ru.get("crowded_top") or []; room = ru.get("room_bottom") or []
    holdT = ru.get("holdT_top") or []
    healthy = bool(ru.get("rotation_healthy")); sucking = bool(ru.get("mainline_sucking"))
    positions = held_positions(); cm = concepts_map(); today = _today()
    # 卖侧 legs
    sell_legs = []
    for p in positions:
        chs = chains_of(p["symbol"], cm)
        hit = [c for c in chs if c in crowded or c in holdT]
        if hit:
            sell_legs.append({"symbol": p["symbol"], "chain": hit[0]})
    # 买侧候选链
    buy_chains = []
    for c in room + holdT:
        is_main = any(t in c or c in t for x in [str(ml.get("main_line") or ""), " ".join(str(v) for v in (ml.get("candidates") or []))] for t in x.split("/"))
        if is_main:
            buy_chains.append((c, "mainline"))
        elif wop in ("t_only", "side", "defense", "exit") and healthy and not sucking:
            buy_chains.append((c, "defensive_resource"))
    held_syms = {p["symbol"] for p in positions}
    buy_legs = []
    for chain, side in buy_chains[:2]:
        for cand in pick_buy(chain, exclude=held_syms, limit=3):
            buy_legs.append({"symbol": cand["symbol"], "chain": chain, "side": side})
    print("DECISION sell_legs", sell_legs, "buy_chains", buy_chains, "buy_legs", buy_legs)
    if dry:
        json.dump({"mode": "SWITCH_ARM_DRY", "date": today, "sell_legs": sell_legs,
                   "buy_legs": buy_legs, "wave": wop, "healthy": healthy, "sucking": sucking},
                  open(os.path.join(DATA, "rotation_switch_arm_dry.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("DRY-RUN 未写库，见 data/rotation_switch_arm_dry.json")
        return 0
    import psycopg2
    conn = psycopg2.connect(DB); conn.autocommit = True; cur = conn.cursor()
    expire_old(cur, today)
    armed = []
    for s in sell_legs:
        rid = arm(conn, cur, s["symbol"], "custom", "sell", SELL_EXPR, today)
        armed.append({"type": "sell_vwap_break", "symbol": s["symbol"], "id": rid})
    for b in buy_legs:
        rid253 = arm(conn, cur, b["symbol"], "custom_m5dump", "buy", BUY_253_EXPR, today)
        rid254 = arm(conn, cur, b["symbol"], "custom_prevlow", "buy", BUY_254_EXPR, today)
        armed.append({"type": "buy_253", "symbol": b["symbol"], "id": rid253})
        armed.append({"type": "buy_254", "symbol": b["symbol"], "id": rid254})
    cur.close(); conn.close()
    print("ARMED", armed)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
