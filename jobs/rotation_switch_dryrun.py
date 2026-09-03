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
    plan = {"ts": datetime.now().isoformat(), "mode": "DRY-RUN(不自动下单)", "wave": wave.get("operation"),
            "wave_sub": wave.get("sub_level"), "chain_signals": {k: v for k, v in signals.items() if v != "normal"},
            "sell_plan": sell_plan, "buy_chains": buy_chains,
            "defaults_note": "卖旧尺度状态机/先卖后分步买/名单3-5档位probe3-refill8-add5 均为系统假设(非狼大原话)，dry-run观察校准"}
    path = os.path.join(DATA, "rotation_switch_plan.json")
    json.dump(plan, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", path)
    print(json.dumps(plan, ensure_ascii=False, indent=1)[:1500])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
