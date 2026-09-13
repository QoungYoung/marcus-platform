# -*- coding: utf-8 -*-
"""rotation_switch_agent.py — 主线内切换·Agent(Pi/dsh)链级决策 + 模拟盘直接执行（代码护栏兜底）"""
import os, sys, json, time
import urllib.request
sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "data")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")
CHAT_URL = os.getenv("WAVE_CHAT_URL", "http://marcus-dsh:3001/chat")
API = os.getenv("MARCUS_API_URL", "http://backend:8000/api/v1")
FENCE = chr(96) * 3

def _relay():
    """加载 core/tushare_relay.py（2026-09-13 起 datahubco 基础接口 + promax 聚合接口，
    替代已失效的 gzcloud 代理）。"""
    import importlib, pathlib, sys
    try:
        return importlib.import_module("tushare_relay")
    except ImportError:
        pass
    for _p in pathlib.Path(__file__).resolve().parents:
        if (_p / "core" / "tushare_relay.py").exists():
            sys.path.insert(0, str(_p / "core"))
            return importlib.import_module("tushare_relay")
    raise ImportError("core/tushare_relay.py 未找到")


def _ts_daily_rows(ts_code, start_date, end_date, fields="ts_code,trade_date,close"):
    """Tushare 中继 daily 查询（返回 items 行列表，字段序 = fields）。"""
    _f, items = _relay().relay_items("daily", ts_code=ts_code, start_date=start_date,
                                     end_date=end_date, fields=fields)
    return items or []

def load(name):
    try:
        return json.load(open(os.path.join(DATA, name), encoding="utf-8"))
    except Exception:
        return {}

def norm(s):
    return str(s).replace(" ", "").replace("　", "")

def _http(path, payload):
    req = urllib.request.Request(API + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def _latest_close(ts):
    rows = _ts_daily_rows(ts, "20260101", "20260901")
    return float(rows[-1][2]) if rows else 0.0

def _held():
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT symbol, volume FROM paper_positions WHERE account_id='stock' AND volume>0")
        out = [{"symbol": str(r[0]), "volume": int(r[1] or 0)} for r in cur.fetchall()]
        cur.close(); conn.close()
        return out
    except Exception:
        return []

def _concepts():
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

def _chains_of(sym, concepts):
    from rotation_universe import get_sub_universe
    SU = get_sub_universe()
    ts = sym[2:] + ("." + sym[:2])
    names = concepts.get(sym, []) + concepts.get(ts, [])
    return [sub for sub, kws in SU.items() if any(norm(k) in norm(n) for n in names for k in kws)]

def _in_exec_window():
    from datetime import datetime
    hm = datetime.now().hour * 100 + datetime.now().minute
    return (945 <= hm <= 1125) or (1300 <= hm <= 1440)

def _live_price(ts, xq):
    try:
        sys.path.insert(0, "/app/app")
        from app.services.t_data_sources import fetch_tencent_quote
        q = fetch_tencent_quote(xq)
        if q and q.get("current"):
            return float(q["current"])
    except Exception:
        pass
    return _latest_close(ts)


def _bad_stock_set():
    bad = set()
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT ts_code, name, is_st FROM stock_pool")
        for ts, name, st in cur.fetchall():
            n = str(name or "").upper()
            if int(st or 0) == 1 or n.startswith("ST") or n.startswith("*ST"):
                bad.add(str(ts))
        cur.execute("SELECT symbol, flag_type, value FROM risk_flags WHERE flag_type IN ('is_st','earnings_bad')")
        for sym, ft, val in cur.fetchall():
            if str(ft) == 'is_st' and str(val) == '1':
                bad.add(str(sym))
            elif str(ft) == 'earnings_bad':
                bad.add(str(sym))
        cur.close(); conn.close()
    except Exception:
        pass
    return bad

def _sell_st_holdings(positions):
    bad = _bad_stock_set()
    out = []
    for p in positions:
        ts = p["symbol"][2:] + "." + p["symbol"][:2]
        if ts not in bad:
            continue
        price = _live_price(ts, p["symbol"])
        if price <= 0:
            continue
        try:
            resp = _http("/trades", {"symbol": p["symbol"], "side": "sell", "price": price,
                                     "volume": int(p.get("volume") or 0), "account": "stock",
                                     "reason": "cleanup_st_holdings(不应持有ST)"})
            out.append({"symbol": p["symbol"], "status": resp.get("status"), "reason": resp.get("reason")})
        except Exception as e:
            out.append({"symbol": p["symbol"], "status": "ERR " + str(e)[:60]})
    return out

def _chain_in_mainline(chain, ml):
    if not ml:
        return False
    text = str(ml.get("main_line") or "") + " " + " ".join(str(x) for x in (ml.get("candidates") or []))
    toks = {t for x in text.split("/") for t in x.split() if len(t) >= 2}
    return any(t in chain or chain in t for t in toks)

def _buy_shortlist(chain, exclude, limit=3):
    from rotation_universe import get_sub_universe
    kws = get_sub_universe().get(chain) or []
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
    bl = load("crowding_blacklist.json") or {}
    detail = bl.get("symbols_detail") or {}
    bad = _bad_stock_set()
    cands = []
    for ts, names in cm.items():
        if any(norm(k) in norm(n) for n in names for k in kws):
            xq = ("SH" + ts[:6] if ts.endswith(".SH") else ("SZ" + ts[:6] if ts.endswith(".SZ") else ts))
            if xq not in exclude and ts not in detail and ts not in bad:
                cands.append((ts, xq))
    out = []
    for ts, xq in cands[:80]:
        if len(out) >= limit:
            break
        try:
            import pandas as pd
            sys.path.insert(0, "/app/apps/main_line")
            import position_class as pc
            rows = _ts_daily_rows(ts, "20250101", "20260901")
            if len(rows) < 60:
                time.sleep(0.1)
                continue
            ser = pd.Series([float(x[2]) for x in rows],
                            index=pd.to_datetime([str(x[1]) for x in rows], format="%Y%m%d"))
            f = pc.position_features(ser)
            pos = pc.classify(f)["position"] if f else None
            if pos in ("LOW", "MID"):
                out.append({"symbol": xq, "ts_code": ts, "position": pos})
        except Exception:
            pass
        time.sleep(0.1)
    return out

def call_agent(prompt, session="rot_switch_"):
    try:
        import requests
        r = requests.post(CHAT_URL, json={"message": prompt, "session_id": session + str(int(time.time()))},
                          headers={"Content-Type": "application/json"}, timeout=180, verify=False)
        r.raise_for_status()
        return r.json().get("reply", "")
    except Exception as e:
        return "CALL_FAIL " + str(e)[:100]

def parse_reply(reply):
    t = reply.strip()
    stripped = t.replace(FENCE + "json", "").replace(FENCE, "").strip()
    for cand in (stripped, t):
        try:
            return json.loads(cand)
        except Exception:
            pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        for k in range(j, i, -1):
            try:
                return json.loads(t[i:k + 1])
            except Exception:
                pass
    return {"parse_failed": True, "raw": reply[:500]}

def build_prompt(ctx):
    nl = chr(10)
    fmt = json.dumps({"sell": [{"chain": "芯片/半导体", "action": "clear", "reason": ""}],
                       "buy": [{"chain": "军工航天", "side": "mainline", "reason": ""}],
                       "dual_line": False, "note": ""}, ensure_ascii=False)
    return (nl.join(["你是复刻狼大主线内切换的决策 Agent。只输出一个 JSON，不要解释。",
                     "输入：" + json.dumps(ctx, ensure_ascii=False),
                     "决策要求（狼大语义）：",
                     "1) sell：只有当某链处于拥挤无空间/出货周期或语境显示已到出货周期才卖，action 只能 clear(整清, 类似E10麦米全清)/halve(减仓, 类似E12光45→18)/keep。",
                     "2) buy：只能从 room/可埋伏或 holdT 链选；side=mainline 主线内切换；side=defensive_resource 防御/资源第二线(仅非build且轮动健康)。",
                     "3) 不要编造输入没有的链名，不确定就给空数组并写 note。",
                     "输出格式：" + fmt]))

def main():
    wave = load("wave_state.json"); ml = load("main_line_state.json")
    ru = load("rotation_universe_result.json")
    wop = str(wave.get("operation") or "side").lower()
    crowded = ru.get("crowded_top") or []; room = ru.get("room_bottom") or []
    holdT = ru.get("holdT_top") or []
    healthy = bool(ru.get("rotation_healthy"))
    sucking = bool(ru.get("mainline_sucking"))
    positions = _held(); concepts = _concepts()
    _st_cleanup = _sell_st_holdings(positions)
    holdings_ctx = [{"symbol": p["symbol"], "volume": p["volume"], "chains": _chains_of(p["symbol"], concepts)} for p in positions]
    ctx = {"date": wave.get("date") or "", "wave_op": wop, "wave_sub": wave.get("sub_level"),
           "main_line": ml.get("main_line"), "candidates": ml.get("candidates"),
           "crowded_no_space": crowded, "room": room, "holdT": holdT,
           "rotation_healthy": healthy, "mainline_sucking": sucking, "holdings": holdings_ctx}
    reply = call_agent(build_prompt(ctx))
    dec = parse_reply(reply)
    allowed_sell = set(crowded) | set(holdT)
    sell_list = []
    for it in (dec.get("sell") or []):
        ch = it.get("chain")
        if ch not in allowed_sell:
            continue
        act = it.get("action") if it.get("action") in ("clear", "halve", "keep") else "keep"
        sell_list.append({"chain": ch, "action": act, "reason": str(it.get("reason") or "")[:100]})
    buy_list = []
    for it in (dec.get("buy") or []):
        ch = it.get("chain"); side = it.get("side")
        if ch not in (set(room) | set(holdT)):
            continue
        if side == "mainline" and not _chain_in_mainline(ch, ml):
            continue
        if side == "defensive_resource" and not (wop in ("t_only", "side", "defense", "exit") and healthy and not sucking):
            continue
        if side not in ("mainline", "defensive_resource"):
            continue
        buy_list.append({"chain": ch, "side": side, "reason": str(it.get("reason") or "")[:100]})
    if not _in_exec_window():
        from datetime import datetime
        out_wait = {"ts": datetime.now().isoformat(), "mode": "PLAN-ONLY(非交易窗口, 不下单)",
                    "wave": wop, "wave_sub": wave.get("sub_level"),
                    "agent_raw": dec, "guarded_sell": sell_list, "guarded_buy": buy_list,
                    "sell_exec": [], "buy_exec": [], "note": "交易日盘中窗口(09:45-11:25/13:00-14:40)才会自动下单"}
        json.dump(out_wait, open(os.path.join(DATA, "rotation_switch_plan.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("非交易窗口 → 只更新计划不下单", flush=True)
        print(json.dumps(out_wait, ensure_ascii=False, indent=1)[:1500])
        return 0
    sell_exec = []
    for s in sell_list:
        for p in positions:
            if s["chain"] not in p.get("chains") or []:
                continue
            hold = int(p.get("volume") or 0)
            vol = hold if s["action"] == "clear" else int(hold / 2 / 100) * 100
            if vol < 100:
                continue
            price = _live_price(p["symbol"][2:] + "." + p["symbol"][:2], p["symbol"])
            if price <= 0:
                continue
            try:
                resp = _http("/trades", {"symbol": p["symbol"], "side": "sell", "price": price,
                                         "volume": vol, "account": "stock",
                                         "reason": "rotation_switch_agent(%s:%s)" % (s["chain"], s["action"])})
                sell_exec.append({"chain": s["chain"], "symbol": p["symbol"], "vol": vol, "status": resp.get("status")})
            except Exception as e:
                sell_exec.append({"chain": s["chain"], "symbol": p["symbol"], "vol": vol, "status": "ERR " + str(e)[:60]})
    buy_exec = []
    held_syms = {p["symbol"] for p in positions}
    for b in buy_list[:2]:
        for cand in _buy_shortlist(b["chain"], exclude=held_syms, limit=3):
            price = _live_price(cand["ts_code"], cand["symbol"])
            if price <= 0:
                continue
            try:
                resp = _http("/trades", {"symbol": cand["symbol"], "side": "buy", "price": price,
                                         "volume": 100, "account": "stock",
                                         "reason": "rotation_switch_agent(%s/%s)" % (b["chain"], b["side"])})
                buy_exec.append({"chain": b["chain"], "symbol": cand["symbol"], "vol": 100, "status": resp.get("status")})
            except Exception as e:
                buy_exec.append({"chain": b["chain"], "symbol": cand["symbol"], "vol": 100, "status": "ERR " + str(e)[:60]})
    from datetime import datetime
    out = {"ts": datetime.now().isoformat(), "mode": "AUTO-EXEC(agent)", "wave": wop, "wave_sub": wave.get("sub_level"),
           "agent_raw": dec, "guarded_sell": sell_list, "guarded_buy": buy_list,
           "sell_exec": sell_exec, "buy_exec": buy_exec}
    path = os.path.join(DATA, "rotation_switch_plan.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", path)
    print(json.dumps({"agent_raw": dec, "guarded_sell": sell_list, "guarded_buy": buy_list,
                      "sell_exec": sell_exec, "buy_exec": buy_exec}, ensure_ascii=False, indent=1)[:2500])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
