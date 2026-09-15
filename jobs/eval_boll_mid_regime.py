# -*- coding: utf-8 -*-
"""eval_boll_mid_regime.py — BOLL 中轨规则的离线验收（2026-09-15 round 16）。

语料（总账 §24.3，XLS 全表定向抽取，跨 2025–2026 一致）：
  · 2025-06-16「只要**板块突破中轨**启动了再重新进去」／2025-07-09「机器人等**板块指数回BOLL中轨再加**啊」
  · 2025-07-10「等下**回中轨企稳缩量再买**就是了」
  · 2025-07-14「如果机器人板块**到日K BOLL中轨就补一点** 没到就拿着 **到上轨**…**就卖一点**」
  · 2025-05-07「**跌破中轨前**还是30%做做T就行了，**跌破就跑路**」／2026-04-23「**大盘BOLL中轨我减仓了**」
  → 规则 = **中轨 = 加仓/建仓资格线；跌破中轨 = 减/不加；上轨 = 卖出条件**。

本脚本只验**买入资格那一半**：板块（链）指数在 BOLL 中轨之上/之下（以及"回中轨"事件）时，
路径 A 的布腿前瞻收益有没有差别。**与 §17（144 线）同法：先按时间分段**，只有跨多个时段方向一致才算证据。

口径：
  · 板块（链）指数 = 该链成分（生产 PG `stock_concept_map` ∩ 本地 bars）**等权收盘**归一化（PIT）；
  · BOLL(20, 2)：中轨 = MA20，上下轨 = ±2σ（他只用"中轨/上轨"这两个字眼，参数用业界标准 20/2）；
  · 腿收益 = `eval_aligned_package` 的 **A 包（现行能布的腿）** 的 253/254 前瞻收益；出场 = 入场后 hold 日收盘；
  · 显著性：ISO 周块状 t；**并给出被拦/被放行日的连续段**（防单段伪重复）。

用法::

    .venv/bin/python jobs/eval_boll_mid_regime.py --hold 5
    .venv/bin/python jobs/eval_boll_mid_regime.py --hold 10 --chains Chiplet概念,光刻机(胶)
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

import eval_pick_selection as E  # noqa: E402
import eval_aligned_package as P  # noqa: E402
import eval_wind_path_a as W  # noqa: E402
from eval_leg_metrics import block_t  # noqa: E402

BOLL_N = 20        # 业界标准（他未给周期，只给"中轨/上轨"）
BOLL_K = 2.0


def chain_index(panel, codes):
    """链成分等权指数（PIT）：以各票首个有效收盘归一化后等权平均。"""
    cols = [panel.ci[c] for c in codes if c in panel.ci]
    if len(cols) < 2:
        return None
    C = panel.close[:, cols]
    idx = np.full(panel.T, np.nan)
    for i in range(panel.T):
        row = C[i]
        base = C[i]
        with np.errstate(invalid="ignore"):
            v = row / base          # 当日归一化分母=当日价 → 需要"起点归一化"
        idx[i] = np.nan
    # 用"每只票首个有效价"作分母做归一化，再等权平均
    norm = np.full_like(C, np.nan)
    for k in range(C.shape[1]):
        col = C[:, k]
        first = next((x for x in col if x == x), None)
        if first:
            norm[:, k] = col / first
    with np.errstate(invalid="ignore"):
        idx = np.nanmean(norm, axis=1)
    return idx


def boll(idx):
    ma = np.full_like(idx, np.nan)
    up = np.full_like(idx, np.nan)
    for i in range(len(idx)):
        if i + 1 < BOLL_N:
            continue
        w = idx[i - BOLL_N + 1:i + 1]
        w = w[~np.isnan(w)]
        if len(w) < BOLL_N:
            continue
        m = float(np.mean(w))
        s = float(np.std(w, ddof=0))
        ma[i] = m
        up[i] = m + BOLL_K * s
    return ma, up


def episodes(days):
    """把日期列表切成连续段（允许间隔 ≤3 个交易日算同一段）。"""
    if not days:
        return []
    out, cur = [], [days[0]]
    for a, b in zip(days, days[1:]):
        if (dt.date(int(b[:4]), int(b[4:6]), int(b[6:8])) - dt.date(int(a[:4]), int(a[4:6]), int(a[6:8]))).days <= 5:
            cur.append(b)
        else:
            out.append((cur[0], cur[-1], len(cur)))
            cur = [b]
    out.append((cur[0], cur[-1], len(cur)))
    return out


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
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_boll_mid.json"))
    args = ap.parse_args()

    chains = W.load_chains()
    if args.chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = W.load_concepts()
    panel = E.Panel(args.bars)
    recs, _lc, _la = P.evaluate(panel, chains, cmap, bad, args.start, args.end,
                                hold=args.hold, limit=args.limit)
    print("[boll] (日,链) %d | 链 %d | hold=%d | BOLL(%d, %.0f)" % (len(recs), len(chains), args.hold, BOLL_N, BOLL_K))

    # 每条链的指数与 BOLL 状态
    state = {}          # (date, chain) -> {"mid":, "up":, "above":, "near_mid_pct":}
    for chain, kws in sorted(chains.items()):
        kws = [W.norm(k) for k in (kws or []) if k]
        cands = [ts for ts, cns in cmap.items() if ts not in bad and any(k in W.norm(c) for k in kws for c in cns)]
        idx = chain_index(panel, cands)
        if idx is None:
            continue
        ma, up = boll(idx)
        for i, d in enumerate(panel.dates):
            if not (args.start <= d <= args.end) or ma[i] != ma[i]:
                continue
            state[(d, chain)] = {"mid": float(ma[i]), "up": float(up[i]), "close": float(idx[i]),
                                 "above": bool(idx[i] >= ma[i]),
                                 "near_mid_pct": float((idx[i] / ma[i] - 1.0) * 100.0)}
    print("[boll] 有 BOLL 状态的 (日,链) %d" % len(state))

    out = {}
    for key in ("253", "254"):
        for label, fn in (("中轨上方（放行）", lambda s: s["above"]), ("中轨下方（拦）", lambda s: not s["above"])):
            pairs = [(r["date"], r["A"][key]) for r in recs if r["A"].get(key) is not None
                     and (r["date"], r["chain"]) in state and fn(state[(r["date"], r["chain"])])]
            out["%s_%s" % (key, "above" if "上方" in label else "below")] = stat(pairs)
    for key in ("253", "254"):
        a = out["%s_above" % key]
        b = out["%s_below" % key]
        print("\n== %s ==" % key)
        print("   中轨上方 %s" % a)
        print("   中轨下方 %s" % b)
        days_below = sorted({r["date"] for r in recs if r["A"].get(key) is not None
                             and (r["date"], r["chain"]) in state and not state[(r["date"], r["chain"])]["above"]})
        print("   中轨下方日：%d 天 → 连续段 %s" % (len(days_below), episodes(days_below)[:6]))

    # 事件研究：**跌破中轨**当天（前一日在上方）布腿的前瞻收益 vs 其它日子
    ev = collections.defaultdict(list)
    for r in recs:
        d, ch = r["date"], r["chain"]
        if (d, ch) not in state:
            continue
        i = panel.di[d]
        prev = None
        for j in range(i - 1, max(-1, i - 6), -1):
            k = (panel.dates[j], ch)
            if k in state:
                prev = state[k]
                break
        s = state[(d, ch)]
        tag = "跌破中轨" if (prev and prev["above"] and not s["above"]) else (
            "站上中轨" if (prev and not prev["above"] and s["above"]) else "其它")
        for key in ("253", "254"):
            if r["A"].get(key) is not None:
                ev[(key, tag)].append((d, r["A"][key]))
    print("\n== 事件研究（253/254 前瞻收益）==")
    for key in ("253", "254"):
        for tag in ("跌破中轨", "站上中轨", "其它"):
            s = stat(ev[(key, tag)])
            if s.get("n"):
                days = sorted({d for d, _ in ev[(key, tag)]})
                # 逐"事件段"均值（判"是否只在单段里成立"的决定性检查）
                m = {d: v for d, v in ev[(key, tag)]}
                segs = episodes(days)
                seg_means = []
                for a, b, _n in segs:
                    vals = [v for d, v in m.items() if a <= d <= b]
                    if vals:
                        seg_means.append(round(float(np.mean(vals)), 2))
                neg = sum(1 for x in seg_means if x < 0)
                print("       逐段均值 %s → %d/%d 段为负" % (seg_means, neg, len(seg_means)))
                print("   %s %-8s %s\n        段(%d): %s" % (key, tag, s, len(episodes(days)), episodes(days)))

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"hold": args.hold, "n_recs": len(recs), "states": out}, open(args.json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n[boll] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
