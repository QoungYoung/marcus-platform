# -*- coding: utf-8 -*-
"""quant_rel_weak.py —— 「个股相对板块走弱」判据的**网格量化**（账本 §9.560）

规则：持仓中，个股当日收益 − 其所属块的等权收益 ≤ −X 点 ⇒ **动作**
  · 动作 A：**清仓**（按当日收盘 ✓）
  · 动作 B：**减半** ✓
  · 对照：**不动**（持到 T+5／T+10 ✓）
度量：全部相对**触发日的前一日收盘** ✓ ⇒ 差 = 动作 − 不动（正值 ⇒ 动作更优 ✓）
另附：**2 月反向**诊断（触发后 10 日**板块本身**的等权收益 ✓）
用法：`.venv/bin/python jobs/quant_rel_weak.py`
"""
from __future__ import annotations
import os, sqlite3, statistics as st, sys
import psycopg2

BLOCKS = ["固态电池", "锂电池", "电池技术", "新能源", "新能源车", "电池化学品", "储能", "锂电专用设备"]
WINDOWS = [("20260105", "20260131", "1月"), ("20260201", "20260228", "2月"), ("20260302", "20260428", "3-4月")]
THRESH = [1.0, 2.0, 3.0, 5.0, 8.0]


def main() -> int:
    cn = psycopg2.connect(os.environ.get("DATABASE_URL") or
                          "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    cn.set_session(readonly=True, autocommit=True)
    c = cn.cursor()
    c.execute("SELECT concept_name, ts_code FROM stock_concept_map WHERE concept_name = ANY(%s)", (BLOCKS,))
    memb = {}
    for b, ts in c.fetchall():
        memb.setdefault(b, set()).add(ts)
    db = sqlite3.connect("file:data/_bt_full/bars.sqlite?mode=ro", uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    for d0, d1, tag in WINDOWS:
        days = sorted(r[0] for r in db.execute(
            "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ?", (d0, d1)))
        px = {d: {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
              for d in days}
        blkret = {}
        for d, pv in zip(days[1:], days[:-1]):
            for b, ms in memb.items():
                rs = [px[d][t] / px[pv][t] - 1 for t in ms if t in px[d] and t in px[pv]]
                if rs:
                    blkret.setdefault(d, {})[b] = st.mean(rs) * 100
        rows = []
        for i in range(1, len(days) - 10):
            d, pv = days[i], days[i - 1]
            for t in px[d]:
                if t not in px[pv]:
                    continue
                own = (px[d][t] / px[pv][t] - 1) * 100
                rels = [own - blkret[d][b] for b, ms in memb.items() if t in ms and b in blkret.get(d, {})]
                if not rels:
                    continue
                t5 = px[days[i + 5]].get(t) if i + 5 < len(days) else None
                t10 = px[days[i + 10]].get(t) if i + 10 < len(days) else None
                if t5 is None or t10 is None:
                    continue
                rows.append({"rel": min(rels), "c0": px[pv][t], "c1": px[d][t], "t5": t5, "t10": t10,
                             "blk10": st.mean([blkret[d2].get(b, 0.0) for d2 in days[i + 1:i + 11] for b in
                                               [bb for bb, ms in memb.items() if t in ms]] or [0.0])})
        if not rows:
            continue
        print("  ════ %s（样本 %d ✓）════" % (tag, len(rows)))
        print("     阈值  触发占比   清仓−不动(T+10)   减半−不动(T+10)   清仓−不动(T+5)   减半−不动(T+5)")
        for X in THRESH:
            sel = [r for r in rows if r["rel"] <= -X]
            if not sel:
                continue

            def d(action, k):
                out = []
                for r in sel:
                    hold = (r["t%d" % k] / r["c0"] - 1) * 100
                    if action == "clear":
                        act = (r["c1"] / r["c0"] - 1) * 100
                    else:
                        act = 0.5 * (r["c1"] / r["c0"] - 1) * 100 + 0.5 * hold
                    out.append(act - hold)
                return st.mean(out)
            print("     −%-4.0f %5.0f%%   %+8.2f 个点      %+8.2f 个点     %+8.2f 个点     %+8.2f 个点"
                  % (X, 100.0 * len(sel) / len(rows), d("clear", 10), d("half", 10), d("clear", 5), d("half", 5)))
        sel5 = [r for r in rows if r["rel"] <= -5.0]
        if sel5:
            print("     └ 诊断(−5 点触发后 10 日)**板块等权**均值 ✓: %+.2f%%（负 ⇒ 板块也弱 ✓）"
                  % st.mean(r["blk10"] for r in sel5))
    return 0


if __name__ == "__main__":
    sys.exit(main())
