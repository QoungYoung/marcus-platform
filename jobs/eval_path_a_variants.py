# -*- coding: utf-8 -*-
"""eval_path_a_variants.py — 路径 A 选股的两个候选改造：流动性闸（A4）与组内前 2（B2）（2026-09-15 round 19）。

审计里还挂着的两条（`docs/wolf-buy-gap-audit.md`）：
  · **A4**：路径 A **没有任何流动性/成交额闸**（pick_v2 有 `amt20 ≥ 1 亿`）→「小票就太多了 不好判断」(2026-09-02)
  · **B2**：「后排反倒不能去 **要看好龙头那些**」(2026-01-16)；「点开板块找**中位以下随便买**」被批判(2026-01-12)
    → pick_v2 有「**子概念组内前 2**」，路径 A 只有 leader 排序取前 3

本脚本在同一批 (日,链) 上对比 4 个变体（只改选股口径，不改触发/出场）：
  V0 现行       = leader 降序 → 位置闸 LOW/MID → 取前 limit（=生产 pick_buy）
  V1 +流动性闸  = 先剔 `amt20 < 1 亿`（pick_v2 同值）
  V2 +组内前2   = 按**命中的子概念**分组，各自只保留 leader 前 2
  V3 两者都上

判据：**每条已挂腿的前瞻收益**（253 次日收盘 / 254 回踩挂单，出场 hold 日收盘，净额扣往返 0.1292%），
ISO 周块状 t；并报"腿数变化"（闸门会减少腿，这本身就是代价）。
⚠️ 与前几轮同样的两条纪律：**先看逐段一致性**（§17/§25/§26），配对差若来自入价差异要看机制性差价（§12/§18）。

用法::

    .venv/bin/python jobs/eval_path_a_variants.py --hold 5
"""
import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend"),
           os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, ".dsh-tmp", "wolfbt")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import eval_pick_selection as E  # noqa: E402
import eval_aligned_package as P  # noqa: E402
import eval_wind_path_a as W  # noqa: E402
from eval_leg_metrics import block_t  # noqa: E402

import importlib.util as _ilu  # noqa: E402
_spec = _ilu.spec_from_file_location("arm_pav", os.path.join(ROOT, "jobs", "rotation_switch_arm.py"))
ARM = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(ARM)

FEE = 0.1292
AMT20_MIN_YI = 1.0          # pick_v2 的 MIN_AMT20_YI（我们自设口径，见总账 §4）


def select(panel, i, cols, limit, cmap, kws, amt_gate=False, top2_per_concept=False,
           mv_top=None, mv_resolver=None):
    """变体选股：返回 (legs[列号], 统计信息)。

    `mv_top` = 只保留**该链（板块）内总市值前 N** 的候选 —— 狼大 2025-12-06「判断板块核心标还有 3 个要点：
    1 他是**板块内总市值前 5** 的股…」；`mv_resolver(ts, i)` 给市值（单位无关，只比大小）。
    """
    f = W._feat(panel, i, cols)
    keep = [k for k in range(len(cols)) if f["ok"][k]]
    if not keep:
        return [], {}
    fc = [cols[k] for k in keep]
    amt20 = f["amt20"][keep]
    lead = np.mean([W._pct_rank(np.nan_to_num(x, nan=-1e9))
                    for x in (f["r60"][keep], f["amt20"][keep], f["lim"][keep])], axis=0)
    order = sorted(range(len(fc)), key=lambda k: (-float(lead[k]), panel.codes[fc[k]]))
    if mv_top and mv_resolver is not None:
        mvs = [(mv_resolver(panel.codes[fc[k]], i), k) for k in order]
        mvs = [(m, k) for m, k in mvs if m is not None and m == m]
        mvs.sort(key=lambda x: -x[0])
        keep = {k for _, k in mvs[:mv_top]}
        order = [k for k in order if k in keep]
    if amt_gate:                      # A4：流动性闸（amt20 单位=亿元，与 pick_v2 同）
        order = [k for k in order if amt20[k] >= AMT20_MIN_YI]
    if top2_per_concept:              # B2：按命中的子概念分组，各留 leader 前 2
        seen = collections.Counter()
        kept = []
        for k in order:
            sym = panel.codes[fc[k]]
            cons = [c for c in (cmap.get(sym) or []) if any(kw in W.norm(c) for kw in kws)]
            if not cons:
                kept.append(k)
                continue
            if any(seen[c] < 2 for c in cons):
                for c in cons:
                    seen[c] += 1
                kept.append(k)
        order = kept
    out = []
    for k in order:
        if len(out) >= limit:
            break
        sym = panel.codes[fc[k]]
        if not ARM.board_ok(sym):        # F2：可执行域（与本项目其它验收同口径）
            continue
        closes = [panel.close[j][fc[k]] for j in range(i + 1)]
        closes = [x for x in closes if x == x]
        if W.position_of(closes) in ("LOW", "MID"):
            out.append(fc[k])
    return out, {"n_cand": len(fc), "n_after_gate": len(order)}


def leg_returns(panel, i, legs, hold):
    r253 = panel.fwd_ret(i, hold, 1)
    r254, hit = panel.fwd_ret_254(i, hold, 0.0)
    rows = []
    for j in legs:
        a = None if (r253 is None or r253[j] != r253[j]) else float(r253[j]) - FEE
        b = None if (r254 is None or not hit[j] or r254[j] != r254[j]) else float(r254[j]) - FEE
        rows.append((a, b))
    return rows


def stat(pairs):
    v = [x for _, x in pairs if x is not None]
    if not v:
        return {"n": 0}
    a = np.array(v, dtype=float)
    bt = block_t(pairs)
    return {"n": len(a), "mean": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
            "win": round(float((a > 0).mean()), 3),
            "t": round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), 2)
            if len(a) > 1 and a.std(ddof=1) > 0 else None,
            "block_t": bt.get("t"), "blocks": bt.get("blocks")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chains", default="")
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260911")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--bars", default=E.BARS)
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_path_a_variants.json"))
    args = ap.parse_args()

    chains = W.load_chains()
    if args.chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = W.load_concepts()
    panel = E.Panel(args.bars)
    import pandas as _pd
    _mv = _pd.read_parquet(args.bars, columns=["ts_code", "trade_date", "total_mv"])
    _mv["trade_date"] = _mv["trade_date"].astype(str)
    _mvp = _mv.pivot_table(index="trade_date", columns="ts_code", values="total_mv", aggfunc="last")

    def mv_of(ts, i):
        try:
            return float(_mvp.at[panel.dates[i], ts])
        except Exception:
            return None

    days = [d for d in panel.dates if args.start <= d <= args.end]
    print("[pA] 链 %d | 交易日 %d | hold=%d limit=%d | 费率 %.4f%%" % (len(chains), len(days), args.hold, args.limit, FEE))

    VARIANTS = {"V0 现行": {}, "V1 +流动性闸(amt20≥1亿)": {"amt_gate": True},
                "V2 +组内前2": {"top2_per_concept": True}, "V3 两者都上": {"amt_gate": True, "top2_per_concept": True},
                "V4 +板块内市值前5(他的话)": {"mv_top": 5}, "V5 +板块内市值前20": {"mv_top": 20}}
    res = {v: {0: [], 1: []} for v in VARIANTS}         # 0=253, 1=254
    leg_cnt = collections.Counter()
    per_day_chain = collections.Counter()
    for chain, kws in sorted(chains.items()):
        kws = [W.norm(k) for k in (kws or []) if k]
        if not kws:
            continue
        cands = [ts for ts, cns in cmap.items() if ts not in bad and any(k in W.norm(c) for k in kws for c in cns)]
        cols = [panel.ci[c] for c in cands if c in panel.ci][:W.SHORTLIST]
        if len(cols) < 2:
            continue
        for d in days:
            i = panel.di[d]
            if i < 61:
                continue
            for v, kw in VARIANTS.items():
                legs, _info = select(panel, i, cols, args.limit, cmap, kws,
                                     mv_resolver=mv_of, **kw)
                leg_cnt[v] += len(legs)
                per_day_chain[v] += 1 if legs else 0
                for a, b in leg_returns(panel, i, legs, args.hold):
                    if a is not None:
                        res[v][0].append((d, a))
                    if b is not None:
                        res[v][1].append((d, b))

    print("\n%-26s %8s %8s %10s %10s | %8s %8s" % ("变体", "腿数", "有腿日链", "253 均值", "253 块状t", "254 均值", "254 块状t"))
    out = {}
    for v in VARIANTS:
        s253, s254 = stat(res[v][0]), stat(res[v][1])
        out[v] = {"legs": leg_cnt[v], "day_chains_with_legs": per_day_chain[v], "253": s253, "254": s254}
        print("%-26s %8d %8d %10s %10s | %8s %8s"
              % (v, leg_cnt[v], per_day_chain[v],
                 ("%+.3f%%" % s253["mean"]) if s253.get("n") else "—", s253.get("block_t"),
                 ("%+.3f%%" % s254["mean"]) if s254.get("n") else "—", s254.get("block_t")))

    # 逐周配对：变体 vs V0（同一条腿不一定相同，按周比"该周平均腿收益"）
    def _wk(d):
        import datetime as _dt
        x = _dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).isocalendar()
        return "%04dW%02d" % (x[0], x[1])
    print("\n== 逐周配对（各变体 − V0，周内先取均值；负=更差）==")
    for key, idx in (("253", 0), ("254", 1)):
        base = collections.defaultdict(list)
        for d, x in res["V0 现行"][idx]:
            base[_wk(d)].append(x)
        for v in VARIANTS:
            if v == "V0 现行":
                continue
            cur = collections.defaultdict(list)
            for d, x in res[v][idx]:
                cur[_wk(d)].append(x)
            diffs = [float(np.mean(cur[w])) - float(np.mean(base[w])) for w in sorted(set(cur) & set(base))]
            if len(diffs) < 3:
                print("   %s %-24s 可比周不足" % (key, v))
                continue
            arr = np.array(diffs, dtype=float)
            t = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))) if arr.std(ddof=1) > 0 else None
            print("   %s %-24s %d 周（%d 周为负） 周差均值 %+.3fpp 周级 t=%s"
                  % (key, v, len(diffs), int((arr < 0).sum()), float(arr.mean()), None if t is None else round(t, 2)))
            out["%s_%s_weekly" % (key, v)] = {"n_weeks": len(diffs), "neg": int((arr < 0).sum()),
                                              "mean_diff": round(float(arr.mean()), 3),
                                              "t": None if t is None else round(t, 2)}
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"hold": args.hold, "variants": out}, open(args.json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n[pA] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
