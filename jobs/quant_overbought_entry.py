# -*- coding: utf-8 -*-
"""quant_overbought_entry.py —— 「买入侧超买过滤」量化（账本 §9.572）

**动机**（§9.572 ✓）：301511 的买入日（0303）**RSI14 = 85.8**、**乖离 MA20 = +17.9%** ✗，
量能已从 z20 = +6.81 衰减到 **+0.70** ✗ ⇒ **买在"极度超买 + 量能衰减"的位置** ✓
而他本人把「超买」用在**卖出侧**（获利了结 ✓），**买入侧我们没有这道门** ✗

**本脚本**：按**入场日的 RSI14 / 乖离 MA20** 分桶，看后续 T+5 / T+10 ✓（全市场 ✓ 三窗口 ✓）
用法：`.venv/bin/python jobs/quant_overbought_entry.py`
"""
from __future__ import annotations
import os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOWS = [("20260105", "20260131", "1月"), ("20260201", "20260228", "2月"), ("20260302", "20260428", "3-4月")]


def main() -> int:
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    days = sorted(r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20251201'"))
    px = {}
    for d in days:
        px[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
    idx = {d: i for i, d in enumerate(days)}
    rows = []
    for i in range(20, len(days) - 10):
        d0, d1 = days[i - 1], days[i]
        for ts, c in px[d1].items():
            if ts not in px[d0]:
                continue
            hist = [px[days[i - k]].get(ts) for k in range(0, 15)]
            if any(h is None for h in hist):
                continue
            closes = list(reversed(hist))
            g = l = 0.0
            for k in range(1, len(closes)):
                ch = closes[k] - closes[k - 1]
                g += max(ch, 0.0); l += max(-ch, 0.0)
            rsi = 100.0 * g / (g + l) if (g + l) else 50.0
            ma20v = [px[days[i - k]].get(ts) for k in range(0, 20)]
            if any(x is None for x in ma20v):
                continue
            bias = (c / (sum(ma20v) / 20.0) - 1) * 100
            t5 = px[days[i + 5]].get(ts)
            t10 = px[days[i + 10]].get(ts)
            if not t5 or not t10:
                continue
            rows.append({"day": d1, "rsi": rsi, "bias": bias,
                         "r5": (t5 / c - 1) * 100, "r10": (t10 / c - 1) * 100})
    if not rows:
        print("  样本为空 ✗"); return 0
    print("  样本 ✓: %d 个 (标的,日)" % len(rows))
    for d0, d1, tag in WINDOWS:
        sub = [r for r in rows if d0 <= r["day"] <= d1]
        if len(sub) < 200:
            continue
        print("  ════ %s（%d ✓）════" % (tag, len(sub)))
        for lo, hi, lab in ((0, 60, "RSI<60"), (60, 70, "60~70"), (70, 80, "70~80"),
                            (80, 90, "**80~90**"), (90, 999, "**90+**")):
            sel = [r for r in sub if lo <= r["rsi"] < hi]
            if len(sel) < 50:
                continue
            print("     %-10s n=%6d（%4.1f%%）｜T+5 %+6.2f%%｜T+10 %+6.2f%%｜T+10 左尾 %+7.2f%%"
                  % (lab, len(sel), 100.0 * len(sel) / len(sub),
                     st.mean(r["r5"] for r in sel), st.mean(r["r10"] for r in sel),
                     sorted(r["r10"] for r in sel)[int(len(sel) * 0.05)]))
        for lo, hi, lab in ((-999, 0, "乖离<0"), (0, 10, "0~10%"), (10, 20, "**10~20%**"), (20, 999, "**20%+**")):
            sel = [r for r in sub if lo <= r["bias"] < hi]
            if len(sel) < 50:
                continue
            print("     %-10s n=%6d（%4.1f%%）｜T+5 %+6.2f%%｜T+10 %+6.2f%%｜T+10 左尾 %+7.2f%%"
                  % ("乖离 " + lab, len(sel), 100.0 * len(sel) / len(sub),
                     st.mean(r["r5"] for r in sel), st.mean(r["r10"] for r in sel),
                     sorted(r["r10"] for r in sel)[int(len(sel) * 0.05)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
