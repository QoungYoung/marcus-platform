# -*- coding: utf-8 -*-
"""rotation_switch_dryrun.py — 主线内切换·狼大逻辑 dry-run 观察任务（不自动下单）

输出 data/rotation_switch_plan.json：
  wave/chain_signals(rotation_universe_result 拥挤/可埋伏/主线吸金)
  sell_plan：当前 stock 持仓中落在“拥挤/出货链”的标的按 wolf_sell_scale 给 action(clear/halve/keep)
  buy_chains：主线内可埋伏/有空间且(若属主线)的方向（只给链级，不自动选股下单）
参数默认值=系统假设(非狼大原话)：
  卖旧尺度状态机：拥挤无空间+defense/exit/t_only/side→clear；build+拥挤无空间→halve(高位先撤部分)；
                  拥挤有空间+defense/exit→halve(E12 45→18 型)；ETF/工具型→keep(E08)
  顺序：先卖后买、买入分步(3日内≤2次小额) — 本期只观察不出单
用法: python -u jobs/rotation_switch_dryrun.py
"""
import os, sys, json
from datetime import datetime

sys.path.insert(0, "/app/apps/main_line")
DATA = os.environ.get("DATA_DIR", "data")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")

def load(name):
    try:
        return json.load(open(os.path.join(DATA, name), encoding="utf-8"))
    except Exception:
        return {}

def wolf_sell_scale(wave_op, chain_signal, is_tool=False):
    """链级卖旧尺度状态机（系统假设 v0，标注非狼大原话；来源=E10全清/E12减45→18/E08不清ETF）"""
    if is_tool:
        return "keep"   # ETF/长期工具：不清仓（E08）
    if chain_signal == "crowded_no_space":
        if wave_op in ("defense", "exit", "t_only", "side"):
            return "clear"  # 出货周期/拥挤无空间 → 整链清（E10 型）
        if wave_op == "build":
            return "halve"  # build 内高位先撤部分（B4/B5 型）
    if chain_signal == "crowded_with_space" and wave_op in ("defense", "exit"):
        return "halve"      # 调整期减仓保留空间（E12 45→18 型）
    return "keep"

def norm(s):
    return str(s).replace(" ", "").replace("　", "")

def held_positions():
    try:
        import psycopg2
        conn = psycopg2.connect(DB); cur = conn.cursor()
        cur.execute("SELECT symbol, volume, avg_price FROM paper_positions WHERE account_id='stock' AND volume>0")
        out = [{"symbol": str(r[0]), "volume": int(r[1] or 0), "avg_price": float(r[2] or 0)} for r in cur.fetchall()]
        cur.close(); conn.close()
        return out
    except Exception as e:
        print("positions err", e); return []

def held_concepts():
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

def chain_of_symbol(sym, concepts):
    from rotation_universe import SUB_UNIVERSE as SUB
    names = concepts.get(sym, []) + concepts.get(sym.replace("SH", "").replace("SZ", "") + (".SH" if sym.startswith("SH") else ".SZ"), [])
    hits = []
    for sub, kws in SUB.items():
        if any(norm(k) in norm(n) for n in names for k in kws):
            hits.append(sub)
    return hits


# ── 模拟盘自动执行（SWITCH_EXEC_ENABLED=1；先卖后买，买侧仅主线内 room 链、LOW/MID 非拥挤）──
def _exec_enabled():
    return os.getenv("SWITCH_EXEC_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

def _api():
    return os.getenv("MARCUS_API_URL", "http://backend:8000/api/v1")

def _http(path, payload):
    import urllib.request
    req = urllib.request.Request(_api() + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())

def _latest_close(sym):
    import urllib.request, gzip
    body = {"api_name": "daily", "token": os.getenv("TUSHARE_TOKEN", ""),
            "params": {"ts_code": sym, "start_date": "20260101", "end_date": "20260901"},
            "fields": "ts_code,trade_date,close"}
    req = urllib.request.Request(os.getenv("TUSHARE_API_URL", ""), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    try:
        d = json.loads(raw.decode())
    except UnicodeDecodeError:
        d = json.loads(gzip.decompress(raw).decode())
    rows = d.get("data", {}).get("items") or []
    return float(rows[-1][2]) if rows else 0.0

def _chain_in_mainline(chain, ml):
    if not ml:
        return False
    text = " ".join(str(ml.get("main_line") or "") + " " + " ".join(str(x) for x in (ml.get("candidates") or [])))
    toks = {t for x in text.split("/") for t in x.split() if len(t) >= 2}
    return any(t in chain or chain in t for t in toks)

def _ts_sym(xq):
    return xq[2:] + ("." + xq[:2])

def _q_mid(ts):
    import sys, pandas as pd
    sys.path.insert(0, "/app/apps/main_line")
    import position_class as pc
    _throttle2()
    body = {"api_name": "daily", "token": os.getenv("TUSHARE_TOKEN", ""),
            "params": {"ts_code": ts, "start_date": "20250101", "end_date": "20260901"},
            "fields": "ts_code,trade_date,close"}
    req = urllib.request.Request(os.getenv("TUSHARE_API_URL", ""), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
    raw = urllib.request.urlopen(req, timeout=30).read()
    try:
        d = json.loads(raw.decode())
    except UnicodeDecodeError:
        import gzip; d = json.loads(gzip.decompress(raw).decode())
    rows = d.get("data", {}).get("items") or []
    if not rows:
        return None
    idx = pd.to_datetime([str(x[1]) for x in rows], format="%Y%m%d")
    ser = pd.Series([float(x[2]) for x in rows], index=idx)
    try:
        f = pc.position_features(ser)
        return pc.classify(f)["position"] if f else None
    except Exception:
        return None

def _buy_shortlist(chain, exclude, limit=3):
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
    bl = load("crowding_blacklist.json") or {}
    detail = bl.get("symbols_detail") or {}
    candidates = []
    for ts, names in cm.items():
        if any(norm(k) in norm(n) for n in names for k in kws):
            xq = ("SH" + ts[:6] if ts.endswith(".SH") else ("SZ" + ts[:6] if ts.endswith(".SZ") else ts))
            if xq not in exclude and ts not in detail:
                candidates.append((ts, xq))
    out = []
    for ts, xq in candidates[:80]:
        if len(out) >= limit:
            break
        pos = _q_mid(ts)
        if pos in ("LOW", "MID"):
            out.append({"symbol": xq, "ts_code": ts, "position": pos})
    return out

def _throttle2():
    time.sleep(0.1)

def maybe_execute(plan):
    if not _exec_enabled():
        print("SWITCH_EXEC_ENABLED=0 → 仅 dry-run，不下单", flush=True)
        return
    print("SWITCH_EXEC_ENABLED=1 → 自动执行(模拟盘)", flush=True)
    ml = load("main_line_state.json")
    results = []
    for item in plan.get("sell_plan") or []:
        action = item.get("action")
        if action not in ("clear", "halve"):
            continue
        sym = item["symbol"]; hold = int(item.get("volume") or 0)
        vol = hold if action == "clear" else int(hold / 2 / 100) * 100
        if vol < 100:
            continue
        price = _latest_close(_ts_sym(sym)) or 0
        if price <= 0:
            continue
        try:
            resp = _http("/trades", {"symbol": sym, "side": "sell", "price": price,
                                     "volume": vol, "account": "stock",
                                     "reason": "rotation_switch_auto(%s)" % action})
            results.append({"action": "sell", "symbol": sym, "volume": vol, "resp": resp.get("status")})
            print("SELL", sym, vol, "->", resp.get("status"), resp.get("reason"), flush=True)
        except Exception as e:
            print("SELL ERR", sym, str(e)[:100], flush=True)
    # 买侧：仅主线内 room 链（保守；当前股票账户大概率无命中）
    for bc in plan.get("buy_chains") or []:
        chain = bc.get("chain")
        if not _chain_in_mainline(chain, ml):
            continue
        shortlist = _buy_shortlist(chain, exclude={x["symbol"] for x in plan.get("sell_plan") or []})
        for cand in shortlist:
            price = _latest_close(cand.get("ts_code") or "") or 0
            if price <= 0:
                continue
            vol = 100  # probe 起点，避免放大；后续按 calc_position 校准
            try:
                resp = _http("/trades", {"symbol": cand["symbol"], "side": "buy", "price": price,
                                         "volume": vol, "account": "stock",
                                         "reason": "rotation_switch_auto(buy_chain:%s)" % chain})
                results.append({"action": "buy", "symbol": cand["symbol"], "volume": vol, "resp": resp.get("status")})
                print("BUY", cand["symbol"], vol, "->", resp.get("status"), resp.get("reason"), flush=True)
            except Exception as e:
                print("BUY ERR", cand["symbol"], str(e)[:100], flush=True)
    plan["exec_results"] = results

def main():
    ru = load("rotation_universe_result.json")
    wave = load("wave_state.json")
    wop = str(wave.get("operation") or "side").lower()
    crowded = ru.get("crowded_top") or []
    room = ru.get("room_bottom") or []
    detail = ru.get("detail") or {}
    signals = {s: ("crowded_no_space" if s in crowded else ("crowded_with_space" if s in ru.get("holdT_top") or [] else ("room" if s in room else "normal"))) for s in detail}
    positions = held_positions()
    concepts = held_concepts()
    sell_plan = []
    for p in positions:
        chains = chain_of_symbol(p["symbol"], concepts)
        hit = [c for c in chains if signals.get(c) in ("crowded_no_space", "crowded_with_space")]
        if not hit:
            continue
        sig = signals.get(hit[0], "normal")
        action = wolf_sell_scale(wop, sig)
        sell_plan.append({"symbol": p["symbol"], "volume": p["volume"], "chains": chains,
                          "signal": sig, "action": action, "wave_op": wop,
                          "rule": "拥挤出货链按狼大状态机(系统假设)" if action != "keep" else "keep(工具/不在出货档)"})
    buy_chains = [{"chain": c, "signal": signals.get(c)} for c in room if signals.get(c) == "room"]
    plan = {"ts": datetime.now().isoformat(), "mode": ("AUTO-EXEC(模拟盘)" if _exec_enabled() else "DRY-RUN(不自动下单)"), "wave": wave.get("operation"),
            "wave_sub": wave.get("sub_level"), "chain_signals": {k: v for k, v in signals.items() if v != "normal"},
            "sell_plan": sell_plan, "buy_chains": buy_chains,
            "defaults_note": "卖旧尺度状态机/先卖后分步买/名单3-5档位probe3-refill8-add5 均为系统假设(非狼大原话)，dry-run观察校准"}
    path = os.path.join(DATA, "rotation_switch_plan.json")
    json.dump(plan, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", path)
    print(json.dumps(plan, ensure_ascii=False, indent=1)[:1500])
    maybe_execute(plan)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
