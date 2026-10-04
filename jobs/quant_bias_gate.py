# -*- coding: utf-8 -*-
"""quant_bias_gate.py —— 「乖离门」量化（账本 §9.577）

**两种形态** ✓（都在**主线上会看的票**宽池内 ✓，T+1~T+3 ✓）：
  · **A 硬门**：乖离 MA20 > X ⇒ **不买**（跳过 ✓）⇒ 看"跳过"是否改善池子的均值/尾部 ✓
  · **B 等回踩**：乖离 > X ⇒ **等回到乖离 ≤ Y**（10 日内 ✓）再买 ⇒ 看是否更好 ✓（未回踩 ⇒ 放弃 ✓）
对照：**原样买**（不做任何门 ✓）
指标 ✓：均值 / 中位 / **T+3 左尾 5%** / **跳过比例** / **放弃比例**
用法：`.venv/bin/python jobs/quant_bias_gate.py [--topk 3]`
"""
from __future__ import annotations
import argparse, glob, json, os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def theme_concepts():
    for p in (os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "backend")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import fusion_mainline as fm
    return {str(k): [str(c) for c in (v or [])] for k, v in (getattr(fm, "THEME_CONCEPTS", {}) or {}).items()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    a = ap.parse_args()
    tc = theme_concepts()
    import psycopg2
    cn = psycopg2.connect(os.environ.get("DATABASE_URL") or
                          "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading", connect_timeout=10)
    cn.set_session(readonly=True, autocommit=True)
    c = cn.cursor()
    c.execute("SELECT concept_name, ts_code FROM stock_concept_map")
    byc: dict = {}
    for cn_, ts in c.fetchall():
        byc.setdefault(str(cn_), set()).add(str(ts))
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    days = sorted(r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20260101'"))
    cl = {}
    for d in days:
        cl[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
    idx = {d: i for i, d in enumerate(days)}

    def bias(ts, i):
        c20 = [cl[days[i - k]].get(ts) for k in range(0, 21)]
        if any(x is None for x in c20) or not cl[days[i]].get(ts):
            return None
        return (cl[days[i]][ts] / st.mean(c20) - 1) * 100

    def fwd(ts, i, k=3):
        p = cl[days[i + k]].get(ts)
        c0 = cl[days[i]].get(ts)
        if not p or not c0:
            return None
        return (p / c0 - 1) * 100

    _skip_n = [0]
    ev = []
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "_bt_t35", "2026*", "wolf_mainline_select.json"))):
        day = os.path.basename(os.path.dirname(f))
        if day not in idx:
            continue
        i = idx[day]
        if i < 25 or i + 13 >= len(days):
            continue
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception as _e_q:
            _skip_n[0] += 1
            continue
        r5 = j.get("r5") or {}
        tops = [k for k, _v in sorted(r5.items(), key=lambda kv: -float(kv[1] or 0))[:a.topk]]
        pool = set()
        for th in tops:
            for cn_ in tc.get(th, []):
                pool |= byc.get(cn_, set())
        for ts in pool:
            b0 = bias(ts, i)
            r0 = fwd(ts, i)
            if b0 is None or r0 is None:
                continue
            # B：等回踩到 乖离 <= Y ✓（10 日内）
            bounce = None
            for k in range(1, 11):
                bk = bias(ts, i + k)
                rk = fwd(ts, i + k)
                if bk is None or rk is None:
                    break
                if bk <= 5.0:
                    bounce = rk
                    break
            ev.append({"b": b0, "r": r0, "bounce": bounce})
    print("  ── 乖离门（宽池 ✓，样本 %d ✓，T+3 ✓）──" % len(ev))
    if not ev:
        return 0
    base = sorted(e["r"] for e in ev)
    print("     %-18s %8s %9s %9s %11s" % ("口径", "样本", "均值", "中位", "**左尾5%**"))
    print("     %-18s %8d %+8.2f%% %+8.2f%% %10.2f%%"
          % ("原样买（对照）", len(base), st.mean(base), st.median(base), base[int(len(base) * 0.05)]))
    for X in (10.0, 15.0, 20.0, 25.0):
        # A 硬门：只留乖离 <= X ✓
        A = sorted(e["r"] for e in ev if e["b"] <= X)
        if A:
            print("     %-18s %8d %+8.2f%% %+8.2f%% %10.2f%%"
                  % ("A 硬门 乖离<=%.0f%%" % X, len(A), st.mean(A), st.median(A), A[int(len(A) * 0.05)]))
        # B 等回踩：乖离 > X 的改在回踩后买（未回踩则用原收益 ✓ 保守）
        B = []
        n_bounce = n_giveup = 0
        for e in ev:
            if e["b"] <= X:
                B.append(e["r"])
            elif e["bounce"] is not None:
                B.append(e["bounce"]); n_bounce += 1
            else:
                B.append(e["r"]); n_giveup += 1
        Bs = sorted(B)
        print("     %-18s %8d %+8.2f%% %+8.2f%% %10.2f%%（回踩 %d／放弃 %d ✓）"
              % ("B 等回踩 >%.0f%%" % X, len(Bs), st.mean(Bs), st.median(Bs), Bs[int(len(Bs) * 0.05)],
                 n_bounce, n_giveup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
