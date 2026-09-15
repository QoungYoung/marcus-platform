# -*- coding: utf-8 -*-
"""eval_ma144_combination.py — 144 门「开 vs 关」**组合级**对照（2026-09-15，用户要求）。

与 §17 的分组期望不同，这里回答"开这个门值不值"：被拦日**不布腿、资金空着**（收益记 0），
把每日组合收益串成净值曲线，比较三个口径与基线。

口径（与生产实现一致：allow() = 斜率≥0 为主，斜率算不出才退"收盘≥MA144"，数据缺失 fail-open）：
  · off      = 门关（基线，每天都布）
  · slope20  = ① 斜率(20天) ≥ 0      ← **生产当前实际口径**
  · slope40  = ① 斜率(40天) ≥ 0
  · above    = ② 收盘 ≥ MA144

腿与收益：复用 `jobs/eval_aligned_package.py` 的 A 包（现行**能布**的腿 = 过板块权限后的腿），
253 腿收益取 tol=0.005（现行），254 腿同；每日等权（当天所有腿的均值）。

用法::  python jobs/eval_ma144_combination.py [--hold 5] [--limit 3]
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = "/app" if os.path.isdir("/app/app") else str(Path(__file__).resolve().parent.parent)
for _p in (ROOT, os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend"), os.path.join(ROOT, "apps", "main_line")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np                     # noqa: E402
import eval_pick_selection as E        # noqa: E402
import eval_aligned_package as AP      # noqa: E402
import wolf_ma144_regime as M144       # noqa: E402


def index_rows():
    """指数收盘序列（升序）：先本地缓存 → 本地 parquet/csv → 中继（仅容器内可用）。"""
    try:
        r = M144.closes(refresh=False)
        if r:
            return r
    except Exception as e:
        print("[comb] 缓存不可用 %s" % str(e)[:60])
    d = os.path.join(ROOT, "data", "指数数据", "index_daily")
    for fn in ("000001.SH.parquet", "000001.SH.csv"):
        p = os.path.join(d, fn)
        if not os.path.exists(p):
            continue
        if fn.endswith("parquet"):
            import pandas as pd
            df = pd.read_parquet(p)
            cols = {c.lower(): c for c in df.columns}
            dc, cc = cols.get("trade_date"), cols.get("close")
            return sorted((str(a), float(b)) for a, b in zip(df[dc], df[cc]))
        out = []
        for i, ln in enumerate(open(p, encoding="utf-8")):
            parts = ln.strip().split(",")
            if i == 0 or len(parts) < 2:
                continue
            try:
                out.append((parts[0].replace("-", ""), float(parts[1])))
            except Exception:
                continue
        if out:
            return sorted(out)
    return []


def allow_map(rows, mode, win=20):
    """→ {date8: bool}（PIT：只用 ≤ 当日的收盘；数据不足 fail-open=True）。"""
    out = {}
    for d, _c in rows:
        try:
            st = M144.state_from(rows, as_of=d, win=win)
        except Exception:
            out[d] = True
            continue
        if not st.get("ok"):
            out[d] = True
            continue
        if mode == "above":
            out[d] = bool(st.get("above", True))
        else:
            out[d] = bool(st.get("allow_slope")) if st.get("allow_slope") is not None \
                else bool(st.get("above", True))
    return out


def net_curve(daily):
    """daily: [(date, ret_pct)] → 累计、最大回撤。"""
    nav, peak, mdd = 1.0, 1.0, 0.0
    for _d, r in daily:
        nav *= (1.0 + r / 100.0)
        peak = max(peak, nav)
        mdd = min(mdd, nav / peak - 1.0)
    return (nav - 1.0) * 100.0, mdd * 100.0


def longest_gap(days_sorted, blocked):
    best = cur = 0
    for d in days_sorted:
        if d in blocked:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260911")
    args = ap.parse_args()

    rows = index_rows()
    print("[comb] 指数收盘 %d 条（%s → %s）" % (len(rows), rows[0][0] if rows else "-", rows[-1][0] if rows else "-"))
    if not rows:
        print("[comb] 没有指数数据，无法回放（本地可在容器里跑）")
        return 1

    E.HOLD = args.hold
    panel = E.Panel()
    uni, lead, allc, MS = E.load_universe()
    th_cons, cmap = E.theme_concept_sets()
    bad = set()
    ev = AP.evaluate(panel, th_cons, cmap, bad, args.start, args.end, hold=args.hold, limit=args.limit)
    recs = ev[0]
    print("[comb] A 包记录 %d 条 (日,链)" % len(recs))

    # 每日组合收益：A 包（现行**能布**的腿，tol=0.005）= 每条腿等权（253 与 254 两条腿都算）
    per_day = {}
    for r in recs:
        a = (r.get("A") or {})
        vals = [v for v in (a.get("253"), a.get("254")) if v is not None]
        if not vals:
            continue
        per_day.setdefault(r["date"], []).extend(vals)
    days = sorted(per_day)
    print("[comb] 交易日 %d 天，总腿数 %d" % (len(days), sum(len(v) for v in per_day.values())))

    modes = [("off", None), ("slope20", ("slope", 20)), ("slope40", ("slope", 40)), ("above", ("above", 20))]
    print("\n%-9s %10s %9s %9s %9s %9s %9s" % ("口径", "累计收益%", "日均%", "腿数", "停买天", "最长连停", "最大回撤%"))
    for name, m in modes:
        if m is None:
            allow = {d: True for d in days}
        else:
            mode, win = m
            am = allow_map(rows, "above" if mode == "above" else "slope", win=win)
            allow = {d: am.get(d, True) for d in days}
        daily, legs, blocked = [], 0, []
        for d in days:
            if allow[d]:
                vals = per_day[d]
                daily.append((d, float(np.mean(vals))))
                legs += len(vals)
            else:
                daily.append((d, 0.0))          # 被拦日：资金空着
                blocked.append(d)
        cum, mdd = net_curve(daily)
        print("%-9s %10.2f %9.3f %9d %9d %9d %9.2f"
              % (name, cum, float(np.mean([x for _, x in daily])), legs, len(blocked),
                 longest_gap(days, set(blocked)), mdd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
