# -*- coding: utf-8 -*-
"""eval_neg_event_regime.py — 「方向级利空回避」的离线验收（2026-09-15 round 25）。

语料（xlsx 精读，总账 §33）：
  · 2025-04-03「买的要**避开关税方向**，这东西绝对没完，还有反制一波」→ **方向级**事件回避；
  · 我们现状：只有**个股级**负事件（`wolf_early_stop.negative_event` 读手工文件 `wolf_negative_events.json`，
    实测生产**无该文件 → 该机制空转**），以及个股级的 `earnings_bad` 排除（`bad_set()`）；
    **没有任何"整个方向先别碰"的判据**。

本轮用**唯一有日期的事件数据**（PG `risk_flags` 的 `earnings_bad`，带 `ann_date`）做代理验收：
  · 对每个 (日, 链)：统计该链成分里**近 W 个自然日内公告过业绩暴雷**的只数 `n_neg`（PIT：只用 `ann_date <= 当日`）；
  · 腿收益取 `eval_aligned_package` 的 A 包（现行能布的腿）；出场 = 入场后 hold 日收盘；
  · 分组：`n_neg >= K` vs `n_neg == 0`；**两道时间结构检查**：① 事件段数 ≥3~4；② 逐周配对符号一致。

用法::

    .venv/bin/python jobs/eval_neg_event_regime.py --hold 5 --window 30
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


def load_neg_events():
    """PG risk_flags 的 earnings_bad → {ts_code: [ann_date,...]}（PIT 依据 ann_date）。"""
    from local_pg import DSN, ensure_tunnel
    ensure_tunnel()
    import psycopg2
    conn = psycopg2.connect(**DSN)
    cur = conn.cursor()
    cur.execute("SELECT symbol, ann_date FROM risk_flags WHERE flag_type='earnings_bad' AND ann_date IS NOT NULL")
    out = collections.defaultdict(list)
    for sym, d in cur.fetchall():
        s = str(sym)
        d8 = str(d)[:8]
        if len(d8) == 8 and d8.isdigit():
            out[s].append(d8)
    cur.close()
    conn.close()
    return out


def _days_between(a: str, b: str) -> int:
    try:
        return (dt.date(int(b[:4]), int(b[4:6]), int(b[6:8])) - dt.date(int(a[:4]), int(a[4:6]), int(a[6:8]))).days
    except Exception:
        return 10 ** 6


def n_neg_in_chain(members, neg, day8, window_days):
    """该链成分里，近 window_days 自然日内公告过业绩暴雷的只数（PIT）。"""
    c = 0
    for ts in members:
        for d in neg.get(ts, ()):
            if 0 <= _days_between(d, day8) <= window_days:
                c += 1
                break
    return c


def episodes(days):
    if not days:
        return []
    out, cur = [], [days[0]]
    for a, b in zip(days, days[1:]):
        if _days_between(a, b) <= 5:
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
    ap.add_argument("--window", type=int, default=30, help="近 N 个自然日内公告过业绩暴雷才算（默认 30）")
    ap.add_argument("--k", type=int, default=1, help="链内 n_neg ≥ K 视为方向级利空（默认 1）")
    ap.add_argument("--bars", default=E.BARS)
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_neg_event_regime.json"))
    args = ap.parse_args()

    chains = W.load_chains()
    if args.chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = W.load_concepts()
    neg = load_neg_events()
    print("[neg] earnings_bad 覆盖 %d 只票（PG risk_flags）" % len(neg))
    panel = E.Panel(args.bars)
    recs, _lc, _la = P.evaluate(panel, chains, cmap, bad, args.start, args.end,
                                hold=args.hold, limit=args.limit)
    print("[neg] (日,链) %d | hold=%d | 窗口 %d 天 | K=%d" % (len(recs), args.hold, args.window, args.k))

    # 每条链的成分（用于数 n_neg）
    members = {}
    for chain, kws in chains.items():
        kws = [W.norm(k) for k in (kws or []) if k]
        members[chain] = [ts for ts, cns in cmap.items()
                          if ts not in bad and any(k in W.norm(c) for k in kws for c in cns)]

    rows = []
    for r in recs:
        if not r["A"].get("253") and not r["A"].get("254"):
            continue
        n = n_neg_in_chain(members.get(r["chain"]) or [], neg, r["date"], args.window)
        rows.append({"date": r["date"], "chain": r["chain"], "n_neg": n,
                     "253": r["A"].get("253"), "254": r["A"].get("254")})
    for key in ("253", "254"):
        for label, fn in ((f"方向级利空(n_neg≥{args.k})", lambda x: x["n_neg"] >= args.k),
                          ("无利空(n_neg=0)", lambda x: x["n_neg"] == 0)):
            pairs = [(x["date"], x[key]) for x in rows if x[key] is not None and fn(x)]
            s = stat(pairs)
            days = sorted({d for d, _ in pairs})
            eps = episodes(days)
            print("\n   %s %-18s %s" % (key, label, s))
            print("        段(%d) %s" % (len(eps), eps[:5]))
    # 逐周配对（周内：利空组均值 − 无利空组均值）
    def _wk(d):
        x = dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).isocalendar()
        return "%04dW%02d" % (x[0], x[1])
    print("\n== 逐周配对（利空组 − 无利空组，负=利空组更差）==")
    out = {"rows": rows, "window": args.window, "k": args.k}
    for key in ("253", "254"):
        a = collections.defaultdict(list)
        b = collections.defaultdict(list)
        for x in rows:
            if x[key] is None:
                continue
            (a if x["n_neg"] >= args.k else b)[_wk(x["date"])].append(x[key])
        diffs = [float(np.mean(a[w])) - float(np.mean(b[w])) for w in sorted(set(a) & set(b))]
        if len(diffs) < 3:
            print("   %s 可比周不足（%d）" % (key, len(diffs)))
            continue
        arr = np.array(diffs, dtype=float)
        t = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))) if arr.std(ddof=1) > 0 else None
        print("   %s：%d 周（%d 周为负） 周差均值 %+.3fpp 周级 t=%s"
              % (key, len(diffs), int((arr < 0).sum()), float(arr.mean()), None if t is None else round(t, 2)))
        out["%s_weekly" % key] = {"n_weeks": len(diffs), "neg": int((arr < 0).sum()),
                                  "mean_diff": round(float(arr.mean()), 3),
                                  "t": None if t is None else round(t, 2)}
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump(out, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[neg] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
