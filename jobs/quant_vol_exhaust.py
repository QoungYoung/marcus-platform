# -*- coding: utf-8 -*-
"""quant_vol_exhaust.py —— 「放量突破后量能衰竭 ⇒ 后续表现」量化（账本 §9.573）

**动机**（§9.572 ✓）：301511 突破（0225 z20=+6.81／0226 +2.32 ✓）后量能逐日衰减
  **0227 +1.41 → 0302 +1.40 → 0303 +0.70（买入日 ✗）→ 0305 −0.02 → 0306 −0.32**
  ⇒ ⇒ **量能衰竭可能比 RSI 更早暴露"假突破"** ✓

**口径**：对每个 (标的,日)：
  · 回看 5 日内是否存在**放量突破**（收盘 ≥ 20 日最高收盘 ×1.005 ∧ 量能 z20 ≥ 1.5 ✓）
  · 若存在 ⇒ 取**当日**的 z20 分桶：**枯竭 <0 ／ 0~0.5 ／ 0.5~1.0 ／ 维持 ≥1.0** ✓
  · 度量：自**当日收盘**起 T+5／T+10 ✓（均值／中位／左尾 ✓）
用法：`.venv/bin/python jobs/quant_vol_exhaust.py`
"""
from __future__ import annotations
import os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOWS = [("20260105", "20260131", "1月"), ("20260201", "20260228", "2月"), ("20260302", "20260428", "3-4月")]


def main() -> int:
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    days = sorted(r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20251101'"))
    cl, vl = {}, {}
    for d in days:
        cl[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
        vl[d] = {r[0]: float(r[1] or 0) for r in db.execute("SELECT ts_code, vol FROM bars WHERE trade_date=?", (d,))}
    rows = []
    for i in range(21, len(days) - 10):
        d = days[i]
        for ts, c in cl[d].items():
            hist_c = [cl[days[i - k]].get(ts) for k in range(1, 21)]
            if any(x is None for x in hist_c):
                continue
            zs = []
            ok = False
            for k in range(0, 5):                      # 回看 5 日找突破 ✓
                dd = days[i - k]
                if ts not in cl[dd]:
                    zs.append(None); continue
                v5 = [vl[days[i - k - j]].get(ts) or 0 for j in range(1, 21)]
                if not all(v5):
                    zs.append(None); continue
                z = ((vl[dd].get(ts) or 0) - st.mean(v5)) / (st.pstdev(v5) or 1)
                zs.append(z)
                hi20 = [cl[days[i - k - j]].get(ts) for j in range(1, 21)]
                if any(x is None for x in hi20):
                    continue
                if cl[dd][ts] >= max(hi20) * 1.005 and z >= 1.5:
                    ok = True
            if not ok or zs[0] is None:
                continue
            z_now = zs[0]                                # 当日 z20 ✓
            # ★ 更干净的口径：当日量 / **突破日**量（避开"被突破日撑大的 20 日均值" ✗）
            vb = None
            for k in range(0, 5):
                dd = days[i - k]
                w = vl[dd].get(ts) or 0
                if not w:
                    continue
                hi20b = [cl[days[i - k - j]].get(ts) for j in range(1, 21)]
                v20b = [vl[days[i - k - j]].get(ts) or 0 for j in range(1, 21)]
                if any(x is None for x in hi20b) or not all(v20b):
                    continue
                zb = (w - st.mean(v20b)) / (st.pstdev(v20b) or 1)
                if cl[dd][ts] >= max(hi20b) * 1.005 and zb >= 1.5:
                    vb = w
                    break
            ratio = ((vl[d].get(ts) or 0) / vb) if vb else None
            t5 = cl[days[i + 5]].get(ts)
            t10 = cl[days[i + 10]].get(ts)
            if not t5 or not t10 or c <= 0:
                continue
            if ratio is None:
                continue
            rows.append({"day": d, "z": z_now, "ratio": ratio,
                         "r5": (t5 / c - 1) * 100, "r10": (t10 / c - 1) * 100})
    if not rows:
        print("  样本为空 ✗"); return 0
    print("  样本 ✓（近 5 日内有放量突破的 (标的,日)）: %d 个" % len(rows))
    BUCKETS = [(-999, 0.0, "**枯竭 z<0**"), (0.0, 0.5, "0~0.5"), (0.5, 1.0, "0.5~1.0"), (1.0, 999, "维持 z≥1.0 ✓")]
    for d0, d1, tag in WINDOWS:
        sub = [r for r in rows if d0 <= r["day"] <= d1]
        if len(sub) < 100:
            continue
        print("  ════ %s（%d ✓）════" % (tag, len(sub)))
        for lo, hi, lab in BUCKETS:
            sel = [r for r in sub if lo <= r["z"] < hi]
            if len(sel) < 30:
                continue
            v10 = sorted(r["r10"] for r in sel)
            print("     %-14s n=%6d（%5.1f%%）｜T+5 %+6.2f%%｜T+10 %+6.2f%%｜中位 %+6.2f%%｜左尾 %+7.2f%%"
                  % (lab, len(sel), 100.0 * len(sel) / len(sub),
                     st.mean(r["r5"] for r in sel), st.mean(v10), st.median(v10), v10[int(len(v10) * 0.05)]))
        print("     ── ★ 当日量 /** 突破日量 ** 分桶（更干净 ✓）──")
        for lo, hi, lab in ((0.0, 0.2, "0~0.2 极度缩量"), (0.2, 0.4, "0.2~0.4"),
                            (0.4, 0.6, "0.4~0.6"), (0.6, 999, ">=0.6 量能维持 ✓")):
            sel = [r for r in sub if lo <= r["ratio"] < hi]
            if len(sel) < 30:
                continue
            v10 = sorted(r["r10"] for r in sel)
            print("     %-14s n=%6d（%5.1f%%）｜T+5 %+6.2f%%｜T+10 %+6.2f%%｜中位 %+6.2f%%｜左尾 %+7.2f%%"
                  % (lab, len(sel), 100.0 * len(sel) / len(sub),
                     st.mean(r["r5"] for r in sel), st.mean(v10), st.median(v10), v10[int(len(v10) * 0.05)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
