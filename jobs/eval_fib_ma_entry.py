# -*- coding: utf-8 -*-
"""eval_fib_ma_entry.py — 两条"挂单买"语料规则的离线验收（2026-09-15 round 12）。

狼大原话（round 10 语料复核补出的 #4 / #6）：
  · **0.382 回撤买点**：2026-02-26「我买票喜欢用**黄金分割和均线** 我弄了个简单的EXCEL 想买什么票丢进去就行了
    `=E3-(E3-D3)*0.382`」；2026-03-05「黄金分割**只用0.382和0.618**」；
    2025-07-03「只要**回踩没破前两条红K的0.382**就还能持仓」（持有口径）
  · **均线挂单买**：「好票**跌到事先画好的线上（13/34/60/144）**…波段起头时把自选里**跌下来的都挂上**」（4 条线来自原话）

本脚本回答：**在我们现在已经在看的票上，把入场触发换成这两条，会比现有的 253/254 更好还是更差？**
（不新增标的域、不改选股——只换"什么时候买、按什么价买"。）

口径（全部与阶段 0 同尺子）：
  标的域 = `jobs/eval_aligned_package.py` 的 **A 包（现行：选股后只保留能布的腿）** 的 `sym_A`（同一批票，公平对比）；
  触发与成交：
    · `253`（现行）= 次日收盘入场
    · `254`（现行）= 触发价 前一日最低×(1+tol)，次日盘中触及即成交（tol 0 / 0.005 两档）
    · `FIB`  = 触发价 `H−(H−L)×0.382`，其中 H/L = **最近两根红K**（收盘>开盘）的 高/低（他的公式与口径）
    · `MA13/34/60/144` = 触发价 = 当日**价格下方最近的一条均线**（跌到线上挂单），次日盘中触及即成交
  出场 = 入场后 `hold` 个交易日收盘；净值 = 毛收益 − 往返 **0.1292%**（生产费率模型）；
  未成交 = **资金空着 = 0**，所以报"**每条已挂腿的期望**"（成交率 × 成交均收益），显著性用 ISO 周块状 t。

用法::

    .venv/bin/python jobs/eval_fib_ma_entry.py --hold 5
    .venv/bin/python jobs/eval_fib_ma_entry.py --hold 10 --chains Chiplet概念,光刻机(胶)
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
import pandas as pd  # noqa: E402

import eval_pick_selection as E  # noqa: E402
import eval_aligned_package as P  # noqa: E402
import eval_wind_path_a as W  # noqa: E402
from eval_leg_metrics import block_t  # noqa: E402

FEE = 0.1292      # %，往返（含滑点）—— 生产口径
MA_LINES = (13, 34, 60, 144)   # 他的话（「13/34/60/144」）
FIB = 0.382                    # 他的话


class Bars:
    """mkt_bars_daily → O/H/L/C 矩阵（只用得到 open 与已有面板之外的列）。"""

    def __init__(self, path):
        df = pd.read_parquet(path, columns=["ts_code", "trade_date", "open", "high", "low", "close"])
        self.dates = sorted(df["trade_date"].astype(str).unique())
        self.codes = sorted(df["ts_code"].unique())
        self.di = {d: i for i, d in enumerate(self.dates)}
        self.ci = {c: i for i, c in enumerate(self.codes)}
        n, m = len(self.dates), len(self.codes)
        self.O = np.full((n, m), np.nan)
        self.H = np.full((n, m), np.nan)
        self.L = np.full((n, m), np.nan)
        self.C = np.full((n, m), np.nan)
        ri = df["trade_date"].astype(str).map(self.di).to_numpy()
        cj = df["ts_code"].map(self.ci).to_numpy()
        for col, arr in (("open", self.O), ("high", self.H), ("low", self.L), ("close", self.C)):
            arr[ri, cj] = pd.to_numeric(df[col], errors="coerce").to_numpy()
        self._ma = {}

    def ma(self, k):
        """k 日均线矩阵（PIT：第 i 行=截至 i 的 k 日均值）。"""
        if k not in self._ma:
            self._ma[k] = pd.DataFrame(self.C).rolling(k, min_periods=k).mean().to_numpy()
        return self._ma[k]

    def red_range(self, i, j):
        """最近两根红K（收盘>开盘）的 (H, L)；不足两根 → (None, None)。"""
        found = []
        for t in range(i, max(-1, i - 60), -1):
            o, c, h, l = self.O[t, j], self.C[t, j], self.H[t, j], self.L[t, j]
            if any(x != x for x in (o, c, h, l)):     # NaN
                continue
            if c > o:
                found.append((h, l))
                if len(found) == 2:
                    break
        if len(found) < 2:
            return None, None
        return max(found[0][0], found[1][0]), min(found[0][1], found[1][1])

    def fib_trig(self, i, j, rng="red2"):
        """0.382 回撤触发价 = H − (H−L)×0.382；**必须低于当日收盘**才算"挂单等回踩"，否则不下单。

        区间口径（他的原话只给了公式与"前两条红K"，**区间来源未写明** → 这里把三种口径都算出来做敏感性）：
          · red2  = 最近两根红K（收盘>开盘）的 高/低（2025-07-03 原话口径，但那是**持有**语境）
          · hh20  = 最近 20 个交易日 高/低（⛔我们的代理）
          · hh60  = 最近 60 个交易日 高/低（⛔我们的代理）
        """
        px = self.C[i, j]
        if px != px:
            return None
        if rng == "red2":
            h, l = self.red_range(i, j)
        else:
            n = 20 if rng == "hh20" else 60
            if i + 1 < n:
                return None
            hs, ls = self.H[i - n + 1:i + 1, j], self.L[i - n + 1:i + 1, j]
            hs, ls = hs[~np.isnan(hs)], ls[~np.isnan(ls)]
            if len(hs) < n // 2 or len(ls) < n // 2:
                return None
            h, l = float(np.max(hs)), float(np.min(ls))
        if h is None or l is None or h <= l:
            return None
        trig = h - (h - l) * FIB
        return trig if 0 < trig < px else None      # 只在市价下方挂单

    def ma_below(self, i, j):
        """当日价格下方**最近**的一条均线值（跌到线上挂单）；没有 → None。"""
        px = self.C[i, j]
        if px != px:
            return None
        best = None
        for k in MA_LINES:
            v = self.ma(k)[i, j]
            if v == v and v <= px and (best is None or v > best):
                best = float(v)
        return best

    def fill_ret(self, i, j, trig, hold):
        """触发价 trig（次日盘中触及=成交），出场 = 入场后 hold 日收盘；返回 (净收益%, 是否成交)。"""
        a, b = i + 1, i + 1 + hold
        if trig is None or trig != trig or trig <= 0 or b >= len(self.dates):
            return None, False
        lo = self.L[a, j]
        if lo != lo or lo > trig:
            return None, False
        c = self.C[b, j]
        if c != c:
            return None, False
        return (c / trig - 1.0) * 100.0 - FEE, True

    def close_ret(self, i, j, hold):
        """253 口径：次日收盘入场，hold 日后收盘出场。"""
        a, b = i + 1, i + 1 + hold
        if b >= len(self.dates):
            return None, False
        c0, c1 = self.C[a, j], self.C[b, j]
        if c0 != c0 or c1 != c1 or c0 <= 0:
            return None, False
        return (c1 / c0 - 1.0) * 100.0 - FEE, True


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
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_fib_ma_entry.json"))
    args = ap.parse_args()

    chains = W.load_chains()
    if args.chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = W.load_concepts()
    panel = E.Panel(args.bars)
    recs, _lc, _la = P.evaluate(panel, chains, cmap, bad, args.start, args.end,
                                hold=args.hold, limit=args.limit)
    B = Bars(args.bars)
    print("[fib/ma] (日,链) %d | 链 %d | hold=%d | 往返费率 %.4f%%" % (len(recs), len(chains), args.hold, FEE))

    # 每条"已挂腿"（现行能布的票）上比 5 种触发
    legs = []
    for r in recs:
        i = B.di.get(r["date"])
        if i is None:
            continue
        for sym in (r.get("sym_A") or []):
            j = B.ci.get(sym)
            if j is None or i < 200:
                continue
            legs.append((r["date"], r["chain"], sym, i, j))
    print("      已挂腿样本 %d 条（现行能布的票；同一批票跑所有触发，公平对比）" % len(legs))

    # 触发定义 → (名称, 取值函数)
    defs = [
        ("253 次日收盘", lambda i, j: B.close_ret(i, j, args.hold)),
        ("254 前低 tol0", lambda i, j: B.fill_ret(i, j, B.L[i, j] if B.L[i, j] == B.L[i, j] else None, args.hold)),
        ("254 前低 tol0.005", lambda i, j: B.fill_ret(i, j, (B.L[i, j] * 1.005) if B.L[i, j] == B.L[i, j] else None, args.hold)),
        ("FIB red2(前两根红K)", lambda i, j: B.fill_ret(i, j, B.fib_trig(i, j, "red2"), args.hold)),
        ("FIB hh20(近20日)", lambda i, j: B.fill_ret(i, j, B.fib_trig(i, j, "hh20"), args.hold)),
        ("FIB hh60(近60日)", lambda i, j: B.fill_ret(i, j, B.fib_trig(i, j, "hh60"), args.hold)),
        ("MA 下方最近线", lambda i, j: B.fill_ret(i, j, B.ma_below(i, j), args.hold)),
    ]
    out = {}
    print("\n%-18s %6s %8s %10s %10s %12s" % ("触发", "腿数", "成交率", "成交均收益", "期望/腿", "块状t(成交)"))
    for name, fn in defs:
        pairs, filled, tot = [], [], 0
        for (d, ch, sym, i, j) in legs:
            v, hit = fn(i, j)
            tot += 1
            if hit and v is not None:
                pairs.append((d, v))
                filled.append(v)
        exp = float(np.mean([v for _, v in pairs] + [0.0] * (tot - len(pairs)))) if tot else None
        s = stat(pairs)
        out[name] = {"n_legs": tot, "n_filled": len(filled), "fill_rate": round(len(filled) / max(tot, 1), 4),
                     "filled": s, "expectancy_per_leg": None if exp is None else round(exp, 3),
                     "filled_vals": [round(x, 3) for x in filled]}
        print("%-18s %6d %8.1f%% %10s %10s %12s"
              % (name, tot, 100.0 * len(filled) / max(tot, 1),
                 ("%+.3f%%" % s["mean"]) if s.get("n") else "—",
                 ("%+.3f%%" % exp) if exp is not None else "—", s.get("block_t")))

    # 逐腿配对：FIB / MA 与现成的 254 比"同一条腿"的差（都成交时）
    trig_fn = {"FIB red2(前两根红K)": lambda i, j: B.fib_trig(i, j, "red2"),
               "FIB hh20(近20日)": lambda i, j: B.fib_trig(i, j, "hh20"),
               "FIB hh60(近60日)": lambda i, j: B.fib_trig(i, j, "hh60"),
               "MA 下方最近线": lambda i, j: B.ma_below(i, j),
               "254 前低 tol0": lambda i, j: (B.L[i, j] if B.L[i, j] == B.L[i, j] else None)}
    print("\n逐腿配对（同一条腿、两种触发都成交时；⚠️ 同一条腿只有入场价不同 → 配对差是机制性的）：")
    for nm in ("FIB red2(前两根红K)", "FIB hh20(近20日)", "FIB hh60(近60日)", "MA 下方最近线"):
        # 逐腿可比：直接按腿取两触发值
        d_fib = []
        for (d, ch, sym, i, j) in legs:
            v_f, h_f = dict(defs)[nm](i, j)
            v_2, h_2 = dict(defs)["254 前低 tol0"](i, j)
            if h_f and h_2 and v_f is not None and v_2 is not None:
                d_fib.append((d, v_f - v_2))
        if d_fib:
            s = stat(d_fib)
            # ⚠️ 机制性核对：同一条腿、同一天出场 → 两条触发的唯一差别是**入场价**。
            #    "更低挂单价"必然量出更高收益（close/trig），所以配对差本身就是**机制性**的；
            #    真正可比的是"每条已挂腿的期望"（成交率 × 成交均收益）。这里把机制性差价算出来对照。
            gaps = []
            for (d, ch, sym, i, j) in legs:
                tX = trig_fn[nm](i, j)
                t2 = trig_fn["254 前低 tol0"](i, j)
                if tX and t2 and tX > 0 and t2 > 0:
                    vX, hX = dict(defs)[nm](i, j)
                    v2, h2 = dict(defs)["254 前低 tol0"](i, j)
                    if hX and h2:
                        gaps.append((d, (t2 / tX - 1.0) * 100.0))
            g = stat(gaps)
            print("   %-14s vs 254(tol0)：n=%d 均值 %+.3f%% 块状 t=%s | 机制性入场价差 %+.3f%%（同一条腿只有入场价不同 → 配对差=机制性）"
                  % (nm, s["n"], s["mean"], s.get("block_t"), g.get("mean") or 0.0))
            out["paired_" + nm] = {**s, "mech_gap_pct": g.get("mean")}

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({"hold": args.hold, "start": args.start, "end": args.end, "n_legs": len(legs), **out},
              open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[fib/ma] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
