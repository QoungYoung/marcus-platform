# -*- coding: utf-8 -*-
"""eval_line_regime.py — 「白线在上 ∧ 缩量 → 没有买点」的离线验收（2026-09-15 round 17）。

语料（总账 §24.2，XLS 全表定向抽取，多时点一致）：
  · 2025-07-15「这种**缩量 白线在上**的局**千万别没事加仓**，太重的逢高减减，**60%仓位内**就行了」
  · 2026-03-03「**缩量**反弹到4141-4150区间上不去并且**白线在上**，那就把今天抄底的有盈利的T出去，尽量保持 **70-75%仓位**」
  · 2026-03-25「如果明天冲3950以上的时候发现**缩量 白线在上** 我会**减回50%内** 安全第一」
  · 2026-04-09「**今天白线在上 肯定没有买点** 下午不管涨还是跌 我都会找个地方**减回50%-55%仓位**」
  → 规则 = **白线在上（权重强于小票）∧ 缩量 → 当日没有买点 / 不加仓**。

口径：
  · **白线** = 加权指数（上证 `000001.SH` 收盘收益率，来自 `index_hist.json`）；
  · **黄线** = 不加权（小票）代理 = **全市场等权收益率**（本地 `bars.parquet` 全票当日涨跌幅均值）——⚠️ 我们的代理，他的黄线是行情软件的不加权指数；
  · `side = bai`（白线在上）当 `white_ret > yellow_ret`；`shrink` = **全市场成交额 < 前一日**（他的"缩量"无阈值，用自然口径；敏感性另看 5 日均量比）；
  · 腿收益 = `eval_aligned_package` 的 **A 包（现行能布的腿）** 的 253/254 前瞻收益；出场 = 入场后 hold 日收盘；
  · **两道时间结构检查**（§17/§25 的教训）：① 事件段数 ≥3~4；② **逐段符号一致**。

用法::

    .venv/bin/python jobs/eval_line_regime.py --hold 5
    .venv/bin/python jobs/eval_line_regime.py --hold 10 --shrink-mode ma5
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
import eval_aligned_package as P  # noqa: E402
import eval_wind_path_a as W  # noqa: E402
from eval_leg_metrics import block_t  # noqa: E402

IDX_FILE = os.path.join(ROOT, ".dsh-tmp", "buyside", "index_hist.json")


def market_days(bars_path):
    """全市场（等权收益、总成交额）按日 → DataFrame[date, yellow_ret, amount]。"""
    df = pd.read_parquet(bars_path, columns=["ts_code", "trade_date", "pct_chg", "amount"])
    df["trade_date"] = df["trade_date"].astype(str)
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    g = df.groupby("trade_date").agg(yellow_ret=("pct_chg", "mean"), amount=("amount", "sum"),
                                     n=("ts_code", "size")).reset_index()
    g = g[g["n"] >= 1000].sort_values("trade_date").reset_index(drop=True)
    return g


def white_ret_map():
    d = json.load(open(IDX_FILE, encoding="utf-8"))
    rows = d.get("000001.SH") or []
    out = {}
    prev = None
    for r in sorted(rows, key=lambda x: x["trade_date"]):
        c = float(r["close"])
        if prev:
            out[r["trade_date"]] = (c / prev - 1.0) * 100.0
        prev = c
    return out


def episodes(days):
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
    ap.add_argument("--shrink-mode", default="prev", choices=["prev", "ma5"],
                    help="缩量口径：prev=成交额<前一日 / ma5=成交额<近5日均值")
    ap.add_argument("--bars", default=E.BARS)
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_line_regime.json"))
    args = ap.parse_args()

    chains = W.load_chains()
    if args.chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = W.load_concepts()
    panel = E.Panel(args.bars)
    recs, _lc, _la = P.evaluate(panel, chains, cmap, bad, args.start, args.end,
                                hold=args.hold, limit=args.limit)

    mkt = market_days(args.bars)
    wr = white_ret_map()
    mkt["white_ret"] = mkt["trade_date"].map(wr)
    mkt = mkt.dropna(subset=["white_ret"]).reset_index(drop=True)
    mkt["side"] = np.where(mkt["white_ret"] > mkt["yellow_ret"], "bai", "huang")
    if args.shrink_mode == "prev":
        mkt["shrink"] = mkt["amount"] < mkt["amount"].shift(1)
    else:
        mkt["shrink"] = mkt["amount"] < mkt["amount"].rolling(5).mean()
    st = {r["trade_date"]: r for _, r in mkt.iterrows()}
    print("[line] (日,链) %d | 链 %d | hold=%d | 缩量口径=%s | 有黄白线状态的交易日 %d"
          % (len(recs), len(chains), args.hold, args.shrink_mode, len(st)))

    def group(fn):
        return [(r["date"], r["A"][key]) for r in recs if r["A"].get(key) is not None
                and r["date"] in st and fn(st[r["date"]])]

    out = {}
    for key in ("253", "254"):
        for label, fn in (("白线在上∧缩量（他说没买点）", lambda s: s["side"] == "bai" and bool(s["shrink"])),
                          ("白线在上（不限量）", lambda s: s["side"] == "bai"),
                          ("缩量（不限黄白）", lambda s: bool(s["shrink"])),
                          ("其它（放行）", lambda s: not (s["side"] == "bai" and bool(s["shrink"])))):
            pairs = group(fn)
            s = stat(pairs)
            out["%s_%s" % (key, label)] = s
            if not s.get("n"):
                continue
            days = sorted({d for d, _ in pairs})
            eps = episodes(days)
            m = collections.defaultdict(list)
            for d, v in pairs:
                m[d].append(v)
            seg_means = []
            for a, b, _n in eps:
                vals = [x for d, vs in m.items() if a <= d <= b for x in vs]
                if vals:
                    seg_means.append(round(float(np.mean(vals)), 2))
            neg = sum(1 for x in seg_means if x < 0)
            flag = "✅ 段数够且逐段一致" if (len(eps) >= 3 and neg in (0, len(seg_means))) else (
                "⚠️ 段数够但逐段不一致" if len(eps) >= 3 else "⚠️ 段数不足")
            print("\n   %s | %-22s %s" % (key, label, s))
            print("        段(%d) %s" % (len(eps), eps[:6]))
            print("        逐段均值(%d 段) %s → %d 段为负 | %s"
                  % (len(seg_means), seg_means, neg, flag))
            out["%s_%s_seg" % (key, label)] = {"n_episodes": len(eps), "neg_episodes": neg,
                                               "seg_means": seg_means}

    # ── 段内配对检验（比"逐段绝对符号"更对：每段算 组差 → 段级 t）──
    print("\n== 段内配对（每段：'他说没买点' − '放行'）==")
    def _isoweek(d):
        x = dt.date(int(d[:4]), int(d[4:6]), int(d[6:8])).isocalendar()
        return "%04dW%02d" % (x[0], x[1])
    weeks = sorted({_isoweek(r["date"]) for r in recs if r["date"] in st})
    for key in ("253", "254"):
        rows = []
        for wk in weeks:
            a = b = wk       # 用周做配对单位（与 block_t 一致）
            def _in(d):
                return _isoweek(d) == wk
            va = [r["A"][key] for r in recs if r["A"].get(key) is not None and _in(r["date"])
                  and st[r["date"]]["side"] == "bai" and bool(st[r["date"]]["shrink"])]
            vb = [r["A"][key] for r in recs if r["A"].get(key) is not None and _in(r["date"])
                  and not (st[r["date"]]["side"] == "bai" and bool(st[r["date"]]["shrink"]))]
            if va and vb:
                rows.append((a, float(np.mean(va)) - float(np.mean(vb)), len(va), len(vb)))
        if not rows:
            continue
        diffs = np.array([d for _, d, _, _ in rows], dtype=float)
        neg = int((diffs < 0).sum())
        t = float(diffs.mean() / (diffs.std(ddof=1) / np.sqrt(len(diffs)))) if len(diffs) > 1 and diffs.std(ddof=1) > 0 else None
        print("   %s：%d 周有配对（%d 周为负） 周差均值 %+.3fpp  周级 t=%s"
              % (key, len(rows), neg, float(diffs.mean()), None if t is None else round(t, 2)))
        print("      逐周差 %s" % [round(d, 2) for _, d, _, _ in rows])
        out["%s_episode_paired" % key] = {"n_episodes": len(rows), "neg": neg,
                                          "mean_diff": round(float(diffs.mean()), 3),
                                          "t": None if t is None else round(t, 2),
                                          "diffs": [round(d, 2) for _, d, _, _ in rows]}
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"hold": args.hold, "shrink_mode": args.shrink_mode, "n_recs": len(recs), "groups": out},
              open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[line] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
