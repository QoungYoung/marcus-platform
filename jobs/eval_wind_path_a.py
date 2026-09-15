# -*- coding: utf-8 -*-
"""eval_wind_path_a.py — B3 验收：狼大「**龙头风向标死了就不能做了 麻溜的跑就行**」(2026-01-12)
用到**路径 A**（`rotation_switch_arm.pick_buy`）上，到底该不该拦、拦了会不会更好。

背景（`docs/wolf-buy-gap-audit.md` B3）：
  · 路径 B（`wolf_confirm_pick`）有 `WOLF_PICK_WIND_HARD=1` 硬拦（风向标=**等待池第一**）；路径 A **没有**；
  · 而 09-11/09-14/09-15 主题结构门把池内主题全挡掉 → 路径 B 根本没进，**实际布腿全来自路径 A**
    → 这条规则目前"看着有、实际不生效"。

本脚本把路径 A 的口径**离线复刻**一遍再量：
  候选域   = 链关键词匹配 `stock_concept_map`（**生产 PG**，不是本地 sqlite 副本 —— Chiplet 概念 PG 只有 3 只）
  leader  = r60 / amt20 / lim 三分位排名均值（与 `pick_buy` 同式）
  **风向标 = 该链 leader 最高的一只**（老龙头）
  破位判据 = 风向标**收盘** ≤ **前一交易日最低** ×(1−0.5%)（与路径 B `WIND_BREAK` 同值；该阈值本身是 ⛔自设）
  布腿     = leader 降序过位置闸（LOW/MID）取前 `--limit`（=3，与生产调用一致）
  收益     = 次日收盘入场、持 `--hold` 日收盘出场（与阶段 0 尺子一致）；显著性用 **ISO 周块状 t**

判读：**破位日布腿 vs 未破位日布腿**的收益差 → 若破位日明显更差（且块状 t 显著），他的规则对路径 A 也成立；
      若没有差（或反了），就不该硬搬到路径 A（那就只留影子/不接线）。

用法::

    .venv/bin/python jobs/eval_wind_path_a.py --chains Chiplet概念,光刻机(胶) --hold 5
    .venv/bin/python jobs/eval_wind_path_a.py --all-chains --hold 10 --limit 3
"""
import argparse
import collections
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend"),
           os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, ".dsh-tmp", "wolfbt")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import eval_pick_selection as E  # noqa: E402
from eval_leg_metrics import block_t  # noqa: E402

WIND_BREAK = -0.5          # 与 wolf_confirm_pick.WIND_BREAK 同值（⛔自设阈值，见参数总账 §4）
SHORTLIST = 80             # WOLF_PICK_BUY_SHORTLIST 默认（市值预筛后截断；离线无市值→按码序截断）


def norm(s):
    return str(s).replace(" ", "").replace("　", "")


def load_concepts():
    """生产 PG 的概念表 + ST 名单（pick_buy 读的就是 PG）。"""
    from local_pg import DSN, ensure_tunnel          # 本地：进程内 SSH 隧道
    ensure_tunnel()
    import psycopg2
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("SELECT ts_code, concept_name FROM stock_concept_map")
    cmap = collections.defaultdict(list)
    for ts, cn in cur.fetchall():
        cmap[str(ts)].append(str(cn))
    cur.execute("SELECT ts_code FROM stock_pool WHERE is_st=1 OR name LIKE 'ST%' OR name LIKE '*ST%'")
    bad = {str(r[0]) for r in cur.fetchall()}
    cur.close()
    conn.close()
    return cmap, bad


def load_chains():
    """链 → 关键词：优先生产缓存 rotation_sub_universe.json（get_sub_universe 的主路径）。"""
    p = os.path.join(ROOT, ".dsh-tmp", "buyside", "prod_data", "rotation_sub_universe.json")
    try:
        return json.load(open(p, encoding="utf-8"))["subs"]
    except Exception:
        return {}


def _feat(panel, i, cols):
    """pick_buy 的 ② 段：r60 / amt20 / lim（+ 分位排名 → leader）。"""
    C, A, P = panel.close[:i + 1][:, cols], panel.amt[:i + 1][:, cols], panel.pct[:i + 1][:, cols]
    n = np.sum(~np.isnan(C), axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        r60 = (C[i] / C[i - 61] - 1.0) * 100.0
        amt20 = np.nanmean(A[max(0, i - 19):i + 1], axis=0) / 1e5
        lim = np.array([((P[j] / P[j - 1] - 1.0 >= 0.097) & (P[j - 1] > 0)).astype(float)
                        for j in range(max(1, i - 59), i + 1)]).sum(axis=0)
    ok = (~np.isnan(C[i])) & (n >= 61)
    return {"ok": ok, "r60": r60, "amt20": amt20, "lim": lim}


def _pct_rank(v):
    v = np.asarray(v, dtype=float)
    order = np.argsort(np.argsort(v))
    return (order + 1.0) / max(len(v), 1)


def position_of(closes):
    try:
        import position_class as pc
        f = pc.position_features(pd.Series(closes))
        return pc.classify(f)["position"] if f else None
    except Exception:
        return None


def evaluate(panel, chains, cmap, bad, start, end, hold=5, limit=3, shortlist=SHORTLIST, thr=WIND_BREAK):
    days = [d for d in panel.dates if start <= d <= end]
    recs = []
    for chain, kws in sorted(chains.items()):
        kws = [norm(k) for k in (kws or []) if k]
        if not kws:
            continue
        cands = [ts for ts, cns in cmap.items()
                 if ts not in bad and any(k in norm(c) for k in kws for c in cns)]
        cols = [panel.ci[c] for c in cands if c in panel.ci][:shortlist]
        if len(cols) < 2:
            continue
        for d in days:
            i = panel.di[d]
            if i < 61:
                continue
            f = _feat(panel, i, cols)
            if f["ok"].sum() < 1:
                continue
            c = [k for k in range(len(cols)) if f["ok"][k]]
            fc = [cols[k] for k in c]
            r60, amt20, lim = f["r60"][c], f["amt20"][c], f["lim"][c]
            lead = np.mean([_pct_rank(np.nan_to_num(x, nan=-1e9)) for x in (r60, amt20, lim)], axis=0)
            order = sorted(range(len(fc)), key=lambda k: (-float(lead[k]), panel.codes[fc[k]]))
            windk = order[0]
            low_prev = panel.low[i - 1][fc[windk]]
            cl = panel.close[i][fc[windk]]
            dist = ((cl / low_prev - 1.0) * 100.0) if (low_prev and low_prev == low_prev) else 99.0
            broken = bool(dist <= thr)
            # 布腿：leader 降序过位置闸
            legs = []
            for k in order:
                if len(legs) >= limit:
                    break
                closes = [panel.close[j][fc[k]] for j in range(i + 1)]
                closes = [x for x in closes if x == x]
                if position_of(closes) in ("LOW", "MID"):
                    legs.append(fc[k])
            r_fwd = panel.fwd_ret(i, hold, 1)
            if r_fwd is None or not legs:
                continue
            rets = [float(r_fwd[j]) for j in legs if r_fwd[j] == r_fwd[j]]
            if not rets:
                continue
            # 同链等权篮子（该链当日全部候选的前瞻收益均值）——与阶段 0 尺子同口径
            allr = [float(r_fwd[j]) for j in fc if r_fwd[j] == r_fwd[j]]
            basket = float(np.mean(allr)) if allr else None
            rec = {"date": d, "chain": chain, "broken": broken,
                   "wind": panel.codes[fc[windk]], "dist_prevlow_prev": round(float(dist), 2),
                   "n_cand": len(fc), "legs": [panel.codes[j] for j in legs],
                   "ret": round(float(np.mean(rets)), 3),
                   "basket": None if basket is None else round(basket, 3)}
            rec["ex"] = None if basket is None else round(rec["ret"] - basket, 3)
            recs.append(rec)
    return recs


def agg(recs, hold):
    """破位日 vs 未破位日：均值 + ISO 周块状 t（周内先取均值再算 t）。"""
    b = [r for r in recs if r["broken"]]
    k = [r for r in recs if not r["broken"]]
    def _m(rs):
        v = [r["ret"] for r in rs]
        if not v:
            return {"n": 0}
        a = np.array(v, dtype=float)
        out = {"n": len(v), "mean": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
               "win": round(float((a > 0).mean()), 3),
               "t": round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), 2) if len(a) > 1 and a.std(ddof=1) > 0 else None}
        bt = block_t([(r["date"], r["ret"]) for r in rs])
        out["block_t"] = bt.get("t")
        out["blocks"] = bt.get("blocks")
        return out
    # 周内配对差（同周 破位均值 − 未破位均值）
    wb, wk = collections.defaultdict(list), collections.defaultdict(list)
    for r in recs:
        dt8 = dt.date(int(r["date"][:4]), int(r["date"][4:6]), int(r["date"][6:8]))
        iso = dt8.isocalendar()
        key = "%04dW%02d" % (iso[0], iso[1])
        (wb if r["broken"] else wk)[key].append(r["ret"])
    diffs = [(w, float(np.mean(wb[w])) - float(np.mean(wk[w]))) for w in sorted(set(wb) & set(wk))]
    dstat = {"n_weeks": len(diffs), "mean_diff": round(float(np.mean([x for _, x in diffs])), 3) if diffs else None}
    if len(diffs) > 2:
        v = np.array([x for _, x in diffs], dtype=float)
        dstat["t"] = round(float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))), 2) if v.std(ddof=1) > 0 else None
    else:
        dstat["t"] = None
    def _m2(rs, key):
        v = [r[key] for r in rs if r.get(key) is not None]
        if not v:
            return {"n": 0}
        a = np.array(v, dtype=float)
        bt = block_t([(r["date"], r[key]) for r in rs if r.get(key) is not None])
        return {"n": len(v), "mean": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
                "win": round(float((a > 0).mean()), 3), "block_t": bt.get("t"), "blocks": bt.get("blocks")}
    return {"hold": hold, "broken": _m(b), "kept": _m(k), "week_diff": dstat,
            "ex_broken": _m2(b, "ex"), "ex_kept": _m2(k, "ex"),
            "n_chain_days": len(recs), "n_chains": len({r["chain"] for r in recs})}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chains", default="Chiplet概念,光刻机(胶)", help="逗号分隔；--all-chains 则全部")
    ap.add_argument("--all-chains", action="store_true")
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260911")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--wind-thr", type=float, default=WIND_BREAK,
                    help="风向标破位阈值(%%); 默认 -0.5（⛔自设, 参数总账 §4）—— 做敏感性扫描用")
    ap.add_argument("--limit", type=int, default=3, help="与生产 pick_buy(limit=3) 对齐")
    ap.add_argument("--bars", default=E.BARS)
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_wind_path_a.json"))
    args = ap.parse_args()

    chains = load_chains()
    if not args.all_chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = load_concepts()
    panel = E.Panel(args.bars)
    print("[wind-A] 链 %d | 交易日 %s→%s | hold=%d limit=%d | PG 票 %d(ST %d)"
          % (len(chains), args.start, args.end, args.hold, args.limit, len(cmap), len(bad)))

    out = {}
    for hold in ([args.hold] if args.hold else [5, 10]):
        recs = evaluate(panel, chains, cmap, bad, args.start, args.end, hold=hold, limit=args.limit,
                        thr=args.wind_thr)
        a = agg(recs, hold)
        out["hold_%d" % hold] = {"agg": a, "detail": recs}
        print("\n== hold=%d thr=%s%% ==" % (hold, args.wind_thr))
        print("  风向标破位日布腿 : %s" % a["broken"])
        print("  未破位日布腿     : %s" % a["kept"])
        print("  周内配对差       : %s" % a["week_diff"])
        print("  同链超额 破位    : %s" % a["ex_broken"])
        print("  同链超额 未破位  : %s" % a["ex_kept"])
        per_chain = collections.defaultdict(lambda: [0, 0])
        for r in recs:
            per_chain[r["chain"]][0 if r["broken"] else 1] += 1
        for ch, (nb, nk) in sorted(per_chain.items()):
            print("    %-12s 破位 %2d 天 / 未破位 %2d 天" % (ch, nb, nk))
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump(out, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[wind-A] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
