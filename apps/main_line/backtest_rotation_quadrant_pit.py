# -*- coding: utf-8 -*-
"""backtest_rotation_quadrant_pit.py — ③ rotation 双维分类 13 时点【真 PIT】重跑
拥挤维度不再用季度近似(rotation_crowding_<end>)，改用逐时点 PIT 快照:
  data/crowding_pit/stock_crowd_<date>.json
    = 历史 fund_share T-1 top60 + ann_date<=时点最新报告 + 每基金 top10(按 mkv)
聚合回子方向: SUB_UNIVERSE 概念白名单关键词 × stock_concept_map → 每方向 avg_float_per_held/n_held/sum_float。
位置空间维度与 backtest_rotation_quadrant.py 完全一致(concept_hist 距高折让/rel低位/LOW-MID)。
前置: python -u apps/main_line/build_fund_pit.py --dates <13个YYYY-MM-DD>（生成 stock_crowd_*.json）
输出: data/crowding_pit/rotation_crowd_pit_<YYYYMMDD>.json(每点拥挤缓存)
      data/rotation_quadrant_history_pit.json(13时点历史+稳定性) + 与旧口径对照
用法: python -u apps/main_line/backtest_rotation_quadrant_pit.py
"""
import os, sys, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import psycopg2
import pandas as pd
import backtest_rotation_quadrant as brq          # 复用空间维度/pick_dates/quadrant
from rotation_universe import SUB_UNIVERSE as SUB, META_GROUPS, _norm

DATA = os.environ.get("DATA_DIR", "data")
DB = os.getenv("DATABASE_URL", "postgresql://marcus:marcus123@postgres:5432/marcus_trading")

def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

def _iso(date):
    d = str(date).replace("-", "")
    return "%s-%s-%s" % (d[:4], d[4:6], d[6:8]) if len(d) == 8 else str(date)

def stock_crowd_path(date):
    return os.path.join(DATA, "crowding_pit", "stock_crowd_%s.json" % _iso(date))

def pit_crowd_cache_path(date):
    return os.path.join(DATA, "crowding_pit", "rotation_crowd_pit_%s.json" % date.replace("-", ""))

def load_concept_map():
    conn = psycopg2.connect(DB); cur = conn.cursor()
    cur.execute("SELECT concept_name, ts_code FROM stock_concept_map")
    concept_stocks = collections.defaultdict(set)
    for cname, code in cur.fetchall():
        concept_stocks[str(cname)].add(str(code))
    cur.execute("SELECT ts_code, name FROM stock_pool")
    stock_names = {str(r[0]): str(r[1]) for r in cur.fetchall()}
    cur.close(); conn.close()
    return concept_stocks, stock_names

def universe_from_snapshot(snap, concept_stocks, stock_names):
    """build_crowding.py 同款子方向聚合，但 stock 集合=PIT 快照(top60基金top10)而非季度DB全量。"""
    stock = snap.get("stock") or {}
    universe = {}
    for sub, kws in SUB.items():
        names = [c for c in concept_stocks if any(_norm(k) in _norm(c) for k in kws)]
        codes = set()
        for nm in names: codes |= concept_stocks[nm]
        held = [(s, stock[s]) for s in codes if s in stock]
        sum_float = sum(float(v.get("sum_float") or 0) for _, v in held)
        ties = sum(int(v.get("n_funds") or 0) for _, v in held)
        universe[sub] = {
            "n_concepts": len({_norm(n) for n in names}), "concepts": sorted(names)[:12],
            "n_stocks": len(codes), "n_held": len(held),
            "n_funds_ties": ties,
            "sum_float": round(sum_float, 3),
            "avg_float_per_held": round(sum_float / max(len(held), 1), 4),
            "top_held": sorted(held, key=lambda x: (-int(x[1].get("n_funds") or 0),
                                                     -float(x[1].get("sum_float") or 0)))[:6],
        }
    return universe

def ensure_pit_crowd(dates, concept_stocks, stock_names, force=False):
    """逐时点聚合缓存 data/crowding_pit/rotation_crowd_pit_<date>.json"""
    os.makedirs(os.path.join(DATA, "crowding_pit"), exist_ok=True)
    made = []
    for dt in dates:
        p = pit_crowd_cache_path(dt)
        if os.path.exists(p) and not force:
            continue
        sp = stock_crowd_path(dt)
        if not os.path.exists(sp):
            raise RuntimeError("missing snapshot %s — 先跑 build_fund_pit.py --dates %s" % (sp, dt))
        snap = json.load(open(sp, encoding="utf-8"))
        universe = universe_from_snapshot(snap, concept_stocks, stock_names)
        out = {"date": dt, "share_date": snap.get("share_date"), "universe": universe,
               "method": "PIT: fund_share T-1 top60 + ann_date<=date + 每基金top10(按mkv)"}
        json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        made.append(dt)
        n = len(snap.get("stock") or {})
        print("PIT-CROWD", dt, "share", out["share_date"], "held_stocks", n, flush=True)
    if made:
        print("WROTE pit crowd caches:", len(made), "dates", flush=True)
    return made

def stability(history):
    ds = sorted(history)
    trans = collections.Counter(); total = 0
    for a, b in zip(ds, ds[1:]):
        subs = [s for s in history[a] if s in history[b] and s not in META_GROUPS]
        for s in subs:
            total += 1
            if history[a][s]["quadrant"] != history[b][s]["quadrant"]:
                trans[history[a][s]["quadrant"] + "→" + history[b][s]["quadrant"]] += 1
    return {"total_pairs": total, "changes": sum(trans.values()),
            "change_rate": round(sum(trans.values()) / max(total, 1), 3), "by_transition": dict(trans)}

def compare_old(new_hist):
    old = load(os.path.join(DATA, "rotation_quadrant_history.json")).get("history") or {}
    common = changed = 0; diffs = collections.Counter()
    examples = []
    for dt, day in new_hist.items():
        od = old.get(dt) or {}
        for sub, v in day.items():
            ov = od.get(sub)
            if not ov or not ov.get("quadrant") or not v.get("quadrant"): continue
            common += 1
            if ov["quadrant"] != v["quadrant"]:
                changed += 1
                diffs[ov["quadrant"] + "→" + v["quadrant"]] += 1
                if len(examples) < 15:
                    examples.append({"date": dt, "sub": sub, "old": ov["quadrant"], "pit": v["quadrant"],
                                     "old_crowd": ov.get("crowd_avg"), "pit_crowd": v.get("crowd_avg"),
                                     "old_space": ov.get("space"), "pit_space": v.get("space")})
    return {"common_cells": common, "changed_cells": changed,
            "agreement": round((common - changed) / max(common, 1), 3) if common else None,
            "by_transition": dict(diffs), "examples": examples}

def main():
    hist = load(os.path.join(DATA, "concept_hist.json"))
    dates = brq.pick_dates(hist)
    print("dates:", dates, flush=True)
    concept_stocks, stock_names = load_concept_map()
    print("stock_concept_map concepts:", len(concept_stocks), flush=True)
    ensure_pit_crowd(dates, concept_stocks, stock_names)
    history = {}
    for dt in dates:
        crowd = load(pit_crowd_cache_path(dt))
        cu = crowd.get("universe") or {}
        maxc = max([(cu.get(s) or {}).get("avg_float_per_held") or 0 for s in SUB] or [0]) or 1
        rel_map = brq.pc.build_rel_map(hist, upto=dt)
        day = {}
        for sub, kws in SUB.items():
            rows = brq.space_at(hist, dt, kws, rel_map)
            if not rows: continue
            space = sum(r["space"] for r in rows) / len(rows)
            cuv = cu.get(sub) or {}
            cavg = float(cuv.get("avg_float_per_held") or 0.0)
            day[sub] = {"space": round(space, 2), "crowd_avg": cavg,
                        "quadrant": brq.quadrant(cavg, maxc, space), "n": len(rows),
                        "n_held": int(cuv.get("n_held") or 0),
                        "n_concepts": int(cuv.get("n_concepts") or 0),
                        "sum_float": float(cuv.get("sum_float") or 0),
                        "n_funds_ties": int(cuv.get("n_funds_ties") or 0)}
        history[dt] = day
        print(dt, {s: d["quadrant"][:2] for s, d in day.items() if s not in META_GROUPS}, flush=True)
    stab = stability(history)
    cmp = compare_old(history)
    out = {"method": "rotation 双维 13时点真PIT(逐点 fund_share top60+ann_date+top10; 概念白名单 SUB_UNIVERSE)",
           "dates": dates, "history": history, "stability": stab, "vs_old_quarterly": cmp}
    path = os.path.join(DATA, "rotation_quadrant_history_pit.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE", path, flush=True)
    print("PIT stability:", stab, flush=True)
    print("vs old quarterly:", {k: v for k, v in cmp.items() if k != "examples"}, flush=True)
    for ex in cmp.get("examples") or []:
        print("  diff:", ex, flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())