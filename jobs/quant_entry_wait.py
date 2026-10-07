# -*- coding: utf-8 -*-
"""quant_entry_wait.py —— 「放量突破后立刻买」 vs 「等**缩量企稳**再买」量化（账本 §9.561）

他的原话（2025-03-19 ✓）：「日K线W底上穿**带量突破均线  缩量回踩均线  继续带量上涨**」—— 称**顶级教科书级别** ✓
两种买点（同一批**放量突破**候选 ✓）：
  · **A 即刻买**：突破确认的**次日**收盘买入（＝现行 `trend_break_buy` 的 T3 ✓）
  · **B 等缩量企稳**：突破后等**首个**「量比 ≤0.9（缩量 ✓）∧ 收盘 ≥ MA5（企稳 ✓）」的交易日收盘买入 ✓
  · 若 N=10 日内不出现 B 的条件 ⇒ **放弃**（记"未触发" ✓）
度量：各自到 T+5／T+10 的均值/中位/左尾 ✓ ＋ **触发率/放弃率** ✓
用法：`.venv/bin/python jobs/quant_entry_wait.py`
"""
from __future__ import annotations
import os, sqlite3, statistics as st, sys

WINDOWS = [("20260105", "20260131", "1月"), ("20260201", "20260228", "2月"), ("20260302", "20260428", "3-4月")]
BLOCKS = ["固态电池", "锂电池", "电池技术", "新能源", "新能源车", "电池化学品", "储能", "锂电专用设备"]


def main() -> int:
    db = sqlite3.connect("file:data/_bt_full/bars.sqlite?mode=ro", uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    all_days = sorted(r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20260105'"))
    for d0, d1, tag in WINDOWS:
        days = [d for d in all_days if d0 <= d <= d1]
        if len(days) < 20:
            continue
        # 用窗口前后各留缓冲 ✓：突破可能发生在窗口前
        px, vol = {}, {}
        for d in all_days:
            px[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
            vol[d] = {r[0]: float(r[1] or 0) for r in db.execute("SELECT ts_code, vol FROM bars WHERE trade_date=?", (d,))}
        idx = {d: i for i, d in enumerate(all_days)}
        res = {"A": [], "B": []}
        n_break = 0
        for d in days:
            i = idx[d]
            if i < 20 or i + 12 >= len(all_days):
                continue
            for ts, c in px[d].items():
                if ts not in px[all_days[i - 1]]:
                    continue
                his = [px[all_days[i - k]].get(ts) for k in range(1, 21)]
                vs = [vol[all_days[i - k]].get(ts) or 0 for k in range(1, 21)]
                if any(v is None for v in his) or not all(vs):
                    continue
                z = (vol[d][ts] - st.mean(vs)) / (st.pstdev(vs) or 1)
                if c < max(his) * 1.005 or z < 1.5:      # T1 带量突破 ✓
                    continue
                n_break += 1
                # A：次日收盘买 ✓
                da = all_days[i + 1]
                if ts in px[da]:
                    pa = px[da][ts]
                    r5 = ((px[all_days[i + 6]].get(ts) / pa - 1) * 100) if ts in px[all_days[i + 6]] else None
                    r10 = ((px[all_days[i + 11]].get(ts) / pa - 1) * 100) if ts in px[all_days[i + 11]] else None
                    if r10 is not None:
                        res["A"].append((r5, r10))
                # B：突破后 10 日内首个「缩量 ∧ 站上 MA5」✓
                for k in range(1, 11):
                    dd = all_days[i + k]
                    if ts not in px[dd]:
                        break
                    v5 = [vol[all_days[i + k - j]].get(ts) or 0 for j in range(1, 6)]
                    ma5 = [px[all_days[i + k - j]].get(ts) for j in range(0, 5)]
                    if not all(v5) or any(m is None for m in ma5):
                        break
                    vr = (vol[dd][ts] / (sum(v5) / 5.0)) if sum(v5) else 9
                    stab = px[dd][ts] >= (sum(ma5) / 5.0)
                    if vr <= 0.9 and stab:
                        pb = px[dd][ts]
                        j5 = i + k + 5
                        j10 = i + k + 10
                        r5 = ((px[all_days[j5]].get(ts) / pb - 1) * 100) if j5 < len(all_days) and ts in px[all_days[j5]] else None
                        r10 = ((px[all_days[j10]].get(ts) / pb - 1) * 100) if j10 < len(all_days) and ts in px[all_days[j10]] else None
                        if r10 is not None:
                            res["B"].append((r5, r10, k))
                        break
        print("  ════ %s（带量突破 %d 个 ✓）════" % (tag, n_break))
        for key, name in (("A", "A 即刻买（次日收盘 ✓）"), ("B", "B 等缩量企稳 ✓")):
            rows = res[key]
            if not rows:
                print("    %-22s 无样本 ✗" % name); continue
            v5 = sorted(r[0] for r in rows if r[0] is not None)
            v10 = sorted(r[1] for r in rows)
            tag2 = "" if key == "A" else "｜平均等待 %.1f 日" % st.mean(r[2] for r in rows)
            print("    %-22s n=%5d（占突破 %.0f%%）%s｜T+5 均值 %+6.2f%% 中位 %+6.2f%%｜T+10 均值 %+6.2f%% 中位 %+6.2f%% 左尾 %+7.2f%%"
                  % (name, len(rows), 100.0 * len(rows) / max(1, n_break), tag2,
                     st.mean(v5), st.median(v5), st.mean(v10), st.median(v10), v10[int(len(v10) * 0.05)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
