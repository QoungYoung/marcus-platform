# -*- coding: utf-8 -*-
"""quant_short_horizon.py —— **按我们真实持有周期（≤T+3 ✓）**重跑关键量化（账本 §9.574）

**用户口述（2026-10-04）**：「T+10 未免太长了，我们**持有最多 T+3**」✓
⇒ ⇒ 此前 §9.561~§9.573 的结论**全部基于 T+10** ✗ ⇒ 必须按 **T+1／T+2／T+3** 重看 ✓
本脚本一次算出四个维度在 T+1~T+3 的表现 ✓：
  ① **RSI14**（超买 ✓）② **乖离 MA20** ✓ ③ **量能 z20** ✓ ④ **当日量/突破日量**（若近 5 日有放量突破 ✓）
用法：`.venv/bin/python jobs/quant_short_horizon.py [--window 20260302-20260428]`
"""
from __future__ import annotations
import argparse
import os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", default="20260302-20260428")
    a = ap.parse_args()
    d0, d1 = a.window.split("-")
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    days = sorted(r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20251101'"))
    cl, vl = {}, {}
    for d in days:
        cl[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
        vl[d] = {r[0]: float(r[1] or 0) for r in db.execute("SELECT ts_code, vol FROM bars WHERE trade_date=?", (d,))}
    rows = []
    for i in range(25, len(days) - 5):
        d = days[i]
        for ts, c in cl[d].items():
            if c <= 0:
                continue
            c15 = [cl[days[i - k]].get(ts) for k in range(0, 15)]
            c20 = [cl[days[i - k]].get(ts) for k in range(0, 21)]
            v20 = [vl[days[i - k]].get(ts) or 0 for k in range(1, 21)]
            if any(x is None for x in c15) or any(x is None for x in c20) or not all(v20):
                continue
            seq = list(reversed(c15))
            g = l = 0.0
            for k in range(1, len(seq)):
                ch = seq[k] - seq[k - 1]
                g += max(ch, 0.0); l += max(-ch, 0.0)
            rsi = 100.0 * g / (g + l) if (g + l) else 50.0
            bias = (c / (st.mean(c20) if c20 else c) - 1) * 100
            z = ((vl[d].get(ts) or 0) - st.mean(v20)) / (st.pstdev(v20) or 1)
            ratio = None
            for k in range(0, 5):
                dd = days[i - k]
                w = vl[dd].get(ts) or 0
                h20 = [cl[days[i - k - j]].get(ts) for j in range(1, 21)]
                vv = [vl[days[i - k - j]].get(ts) or 0 for j in range(1, 21)]
                if any(x is None for x in h20) or not all(vv):
                    continue
                zb = (w - st.mean(vv)) / (st.pstdev(vv) or 1)
                if cl[dd][ts] >= max(h20) * 1.005 and zb >= 1.5:
                    ratio = (vl[d].get(ts) or 0) / w if w else None
                    break
            fw = {}
            ok = True
            for k in (1, 2, 3, 5):
                p = cl[days[i + k]].get(ts)
                if not p:
                    ok = False; break
                fw[k] = (p / c - 1) * 100
            if not ok:
                continue
            rows.append({"day": d, "rsi": rsi, "bias": bias, "z": z, "ratio": ratio, **{("r%d" % k): v for k, v in fw.items()}})
    sub = [r for r in rows if d0 <= r["day"] <= d1]
    print("  ── 窗口 %s~%s ✓｜样本 %d ✓（**按我们的持有周期 T+1~T+3** ✓）──" % (d0, d1, len(sub)))
    if not sub:
        return 0

    def rep(name, key, buckets):
        print("  ── %s ──" % name)
        print("     %-16s %7s %9s %9s %9s %9s %11s" % ("分组", "占比", "T+1", "T+2", "T+3", "T+5(参考)", "**T+3 左尾5%**"))
        for lo, hi, lab in buckets:
            sel = [r for r in sub if r.get(key) is not None and lo <= r[key] < hi]
            if len(sel) < 40:
                continue
            v3 = sorted(r["r3"] for r in sel)
            print("     %-16s %6.1f%% %+8.2f%% %+8.2f%% %+8.2f%% %+8.2f%% %10.2f%%"
                  % (lab, 100.0 * len(sel) / len(sub),
                     st.mean(r["r1"] for r in sel), st.mean(r["r2"] for r in sel),
                     st.mean(r["r3"] for r in sel), st.mean(r["r5"] for r in sel),
                     v3[int(len(v3) * 0.05)]))
    rep("① RSI14（超买 ✓）", "rsi", [(0, 60, "RSI<60"), (60, 70, "60~70"), (70, 80, "70~80"),
                                     (80, 90, "**80~90**"), (90, 999, "**90+**")])
    rep("② 乖离 MA20 ✓", "bias", [(-999, 0, "乖离<0"), (0, 10, "0~10%"),
                                   (10, 20, "**10~20%**"), (20, 999, "**20%+**")])
    rep("③ 量能 z20 ✓", "z", [(-999, 0, "枯竭<0"), (0, 0.5, "0~0.5"), (0.5, 1.0, "0.5~1.0"), (1.0, 999, "≥1.0")])
    rep("④ 当日量/突破日量（近5日有放量突破 ✓）", "ratio",
        [(0, 0.2, "0~0.2 极度缩"), (0.2, 0.4, "0.2~0.4"), (0.4, 0.6, "0.4~0.6"), (0.6, 999, "≥0.6 维持")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
