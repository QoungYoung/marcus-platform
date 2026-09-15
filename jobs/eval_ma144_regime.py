# -*- coding: utf-8 -*-
"""eval_ma144_regime.py — 验收「**144 线**」在买入侧的用法（2026-09-15 round 10）。

狼大原话（round 10 从语料补齐的 #6）：
  · 2016-07-07「当K线盘整**144线稍微走平**，那就表明**可以做一个波段趋势**了」
  · 2016-08-15「把周线开出来，均线144都已经**走平向上**了。。太棒了，接下来**找买点进大波段**了」
  · 2026-03-20「我的**牛熊分界线是 日K144线**…那是我的**最后底线**」
  · 2026-03-24「哪怕**破了144三天** 我还是要做到我能接受的位置我才出去」
  → 买入侧可落的两条：**① 资格**（144 走平/向上才做波段）**② 触发**（好票跌到 13/34/60/144 线挂单买）。

本脚本只验 **① 资格这一条**（触发那条要新增买腿家族，另立项）：
  把指数 144 均线的状态与**路径 A 实际能布的腿**的前瞻收益对齐，看"不看 144"与"只在 144 不走下时才做"有没有差。

口径：
  · 144 状态：`MA144` 的斜率（`--slope-win` 天，**代理值**，他没给"走平"的量化阈值）；`slope ≥ 0` = 走平/向上（放行），`< 0` = 向下（拦）；
  · 另做**无阈值**的对照：收盘价 在 MA144 之上/之下（他的"牛熊分界线/最后底线"读法）；
  · 买腿：`jobs/eval_aligned_package.py` 的 **A 包（现行：选股后只保留能布的腿）**，253/254 两条入场；
  · 收益：次日收盘入场 / 254 回踩挂单，持 `--hold` 日收盘出场，净额扣往返 0.1292%；ISO 周块状 t。

用法::

    .venv/bin/python jobs/eval_ma144_regime.py --index 000001.SH --hold 5
    .venv/bin/python jobs/eval_ma144_regime.py --index 000300.SH --hold 10 --chains Chiplet概念,光刻机(胶)
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

IDX_FILE = os.path.join(ROOT, ".dsh-tmp", "buyside", "index_hist.json")
MA_N = 144


def load_index(code):
    d = json.load(open(IDX_FILE, encoding="utf-8"))
    rows = d.get(code) or []
    return {r["trade_date"]: float(r["close"]) for r in rows}, [r["trade_date"] for r in rows]


def ma_state(closes_by_day, days, slope_win=20):
    """→ {date: {"ma": MA144, "slope_pct": 斜率%, "above": 收盘≥MA144}}（严格 PIT：只用 ≤ 当日的数据）。"""
    out = {}
    seq = [(d, closes_by_day[d]) for d in days if d in closes_by_day]
    for k in range(MA_N - 1, len(seq)):
        window = [seq[j][1] for j in range(k - MA_N + 1, k + 1)]
        ma = float(np.mean(window))
        if k - slope_win < MA_N - 1:
            slope = None
        else:
            w0 = [seq[j][1] for j in range(k - slope_win - MA_N + 1, k - slope_win + 1)]
            ma0 = float(np.mean(w0))
            slope = (ma / ma0 - 1.0) * 100.0 if ma0 else None
        out[seq[k][0]] = {"ma": round(ma, 2), "slope_pct": None if slope is None else round(slope, 3),
                          "above": bool(seq[k][1] >= ma)}
    return out


def split_stats(recs, day_state, key, allow_fn, hold):
    """按 allow_fn(state) 把 (日,链) 腿收益分两组，算均值/胜率/块状 t。"""
    yes, no = [], []
    for r in recs:
        st = day_state.get(r["date"])
        v = r["A"].get(key)
        if st is None or v is None:
            continue
        (yes if allow_fn(st) else no).append((r["date"], float(v)))
    def _m(pairs):
        if not pairs:
            return {"n": 0}
        a = np.array([v for _, v in pairs], dtype=float)
        bt = block_t(pairs)
        return {"n": len(a), "mean": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
                "win": round(float((a > 0).mean()), 3),
                "t": round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), 2)
                if len(a) > 1 and a.std(ddof=1) > 0 else None,
                "block_t": bt.get("t"), "blocks": bt.get("blocks")}
    return {"allowed": _m(yes), "blocked": _m(no)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="000001.SH")
    ap.add_argument("--chains", default="", help="默认全部（缓存里的所有子方向）")
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260911")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_ma144_regime.json"))
    args = ap.parse_args()

    closes, idx_days = load_index(args.index)
    chains = W.load_chains()
    if args.chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = W.load_concepts()
    panel = E.Panel(E.BARS)

    recs, _lc, _la = P.evaluate(panel, chains, cmap, bad, args.start, args.end,
                                hold=args.hold, limit=args.limit)
    days = sorted({r["date"] for r in recs})
    st = ma_state(closes, idx_days)
    have = [d for d in days if d in st]
    print("[ma144] 指数 %s | 日线到 %s | 腿样本 (日,链) %d（其中 %d 天有指数状态）| hold=%d | 链 %d"
          % (args.index, idx_days[-1], len(recs), len(have), args.hold, len(chains)))

    out = {}
    # ① 斜率（走平/向上才做）—— 敏感性：slope_win = 5/10/20/40
    for k in (5, 10, 20, 40):
        st_k = {d: v for d, v in ma_state(closes, idx_days, slope_win=k).items() if v["slope_pct"] is not None}
        a = split_stats(recs, st_k, "253", lambda s: s["slope_pct"] >= 0, args.hold)
        b = split_stats(recs, st_k, "254", lambda s: s["slope_pct"] >= 0, args.hold)
        n_all = sum(1 for r in recs if r["date"] in st_k)
        n_ok = sum(1 for r in recs if r["date"] in st_k and st_k[r["date"]]["slope_pct"] >= 0)
        out["slope_win_%d" % k] = {"253": a, "254": b, "n_days_known": n_all, "n_days_allowed": n_ok}
        print("\n== ① 144 斜率(窗口 %d 天) ≥ 0 才做：放行 %d/%d 个 (日,链)（%.1f%%） =="
              % (k, n_ok, n_all, 100.0 * n_ok / max(n_all, 1)))
        print("   253 放行: %s" % a["allowed"]); print("   253 被拦: %s" % a["blocked"])
        print("   254 放行: %s" % b["allowed"]); print("   254 被拦: %s" % b["blocked"])
    # ② 无阈值对照：收盘在 MA144 之上
    c = split_stats(recs, st, "253", lambda s: s["above"], args.hold)
    d254 = split_stats(recs, st, "254", lambda s: s["above"], args.hold)
    n_ok = sum(1 for r in recs if r["date"] in st and st[r["date"]]["above"])
    n_all = sum(1 for r in recs if r["date"] in st)
    out["above_ma144"] = {"253": c, "254": d254, "n_days_known": n_all, "n_days_allowed": n_ok}
    print("\n== ② 收盘 ≥ MA144（他的'最后底线'读法）：放行 %d/%d（%.1f%%） =="
          % (n_ok, n_all, 100.0 * n_ok / max(n_all, 1)))
    print("   253 放行: %s" % c["allowed"]); print("   253 被拦: %s" % c["blocked"])
    print("   254 放行: %s" % d254["allowed"]); print("   254 被拦: %s" % d254["blocked"])

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"index": args.index, "hold": args.hold, "start": args.start, "end": args.end,
               "n_recs": len(recs), **out}, open(args.json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n[ma144] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
