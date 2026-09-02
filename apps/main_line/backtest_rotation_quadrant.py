# -*- coding: utf-8 -*-
"""backtest_rotation_quadrant.py — ② rotation 双维分类历史稳定性回测
方法: 用 concept_hist 在 2025-12~2026-08 多个时点回放「位置空间」(距高折让/rel低位/LOW-MID)；
拥挤度取 data/rotation_crowding.json 的 Q2 真实基金 avg_float(注: 单一季度, 回放近似, 非点内)。
输出: data/rotation_quadrant_history.json + 稳定性摘要(相邻时点分类转换率)
用法: python apps/main_line/backtest_rotation_quadrant.py
"""
import os, sys, json, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
import position_class as pc
from rotation_universe import SUB_UNIVERSE, META_GROUPS, _norm, DATA

def load(p):
    try: return json.load(open(p, encoding="utf-8"))
    except Exception: return {}

def close_upto(a, upto):
    t = pd.Timestamp(upto)
    return pd.Series([float(x) for x in a["close"]], index=pd.to_datetime(a["dates"]))[lambda s: s.index <= t]

def pick_dates(hist, start="20251201", end="20260820", step=14):
    cal = sorted({d for a in hist.values() for d in a["dates"]})
    cal = [d for d in cal if start <= d <= end]
    return cal[::step][:14]

def space_at(hist, date, kws, rel_map):
    rows = []
    for code, a in hist.items():
        nm = str(a.get("name") or "")
        if not any(_norm(k) in _norm(nm) for k in kws): continue
        try:
            ser = close_upto(a, date)
            if len(ser) < 80: continue
            f = pc.position_features(ser)
            if not f: continue
            rel = rel_map.get(code)
            f["rel_mainline"] = rel["level"] if rel else None
            cls = pc.classify(f)
            vs = f.get("vs_1y_high_pct")
            dist = max(0.0, min(1.0, (-(vs if isinstance(vs, (int, float)) else 0)) / 30.0))
            rel_lo = 1 if f.get("rel_mainline") == "low" else 0
            lowmid = 1 if cls["position"] in ("LOW", "MID") else 0
            rows.append({"space": min(1.0, 0.45*dist + 0.35*rel_lo + 0.20*lowmid),
                         "vs1y": vs, "pos": cls["position"]})
        except Exception:
            continue
    return rows

def quadrant(crowd_avg, maxc, space):
    crowd = min(1.0, crowd_avg / maxc) if maxc else 0.0
    if crowd >= 0.55 and space < 0.55: return "拥挤无空间"
    if crowd >= 0.55 and space >= 0.55: return "拥挤但有空间"
    if crowd < 0.55 and space >= 0.55: return "可埋伏"
    return "中性"

def quarter_end_of(date):
    d = str(date).replace("-", "").replace(" ", "")
    y = int(d[:4]); m = int(d[4:6])
    if m <= 3: return "%d1231" % (y - 1)
    if m <= 6: return "%d0331" % y
    if m <= 9: return "%d0630" % y
    return "%d0930" % y

def main():
    hist = load(os.path.join(DATA, "concept_hist.json"))
    dates = pick_dates(hist)
    crowd_cache = {}
    history = {}
    for dt in dates:
        qe = quarter_end_of(dt)
        crowd = crowd_cache.get(qe)
        if crowd is None:
            p = os.path.join(DATA, "rotation_crowding_%s.json" % qe)
            crowd = load(p if os.path.exists(p) else os.path.join(DATA, "rotation_crowding.json"))
            crowd_cache[qe] = crowd
        cu = crowd.get("universe") or {}
        maxc = max([(cu.get(s) or {}).get("avg_float_per_held") or 0 for s in SUB_UNIVERSE] or [0]) or 1
        rel_map = pc.build_rel_map(hist, upto=dt)
        day = {}
        for sub, kws in SUB_UNIVERSE.items():
            rows = space_at(hist, dt, kws, rel_map)
            if not rows: continue
            space = sum(r["space"] for r in rows) / len(rows)
            cavg = (cu.get(sub) or {}).get("avg_float_per_held") or 0.0
            day[sub] = {"space": round(space, 2), "crowd_avg": cavg, "quadrant": quadrant(cavg, maxc, space), "n": len(rows)}
        history[dt] = day
        print(dt, "q=", qe, {s: d["quadrant"][:2] for s, d in day.items() if s not in META_GROUPS}, flush=True)
    # 稳定性: 相邻时点转换率
    ds = sorted(history)
    trans = collections.Counter(); total = 0
    for a, b in zip(ds, ds[1:]):
        subs = [s for s in history[a] if s in history[b] and s not in META_GROUPS]
        for s in subs:
            total += 1
            if history[a][s]["quadrant"] != history[b][s]["quadrant"]:
                trans[history[a][s]["quadrant"] + "→" + history[b][s]["quadrant"]] += 1
    out = {"dates": ds, "history": history,
           "stability": {"total_pairs": total, "changes": sum(trans.values()),
                         "change_rate": round(sum(trans.values()) / max(total, 1), 3), "by_transition": dict(trans)}}
    json.dump(out, open(os.path.join(DATA, "rotation_quadrant_history.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("WROTE rotation_quadrant_history.json dates=", len(ds), "total_pairs=", total,
          "change_rate=", out["stability"]["change_rate"])
    print("transitions:", dict(trans))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
