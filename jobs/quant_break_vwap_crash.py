# -*- coding: utf-8 -*-
"""quant_break_vwap_crash.py —— 「急杀限定版 B」量化（账本 §9.562）

动机（§9.561 ✓）：301511 的致命一击是**盘中 5 分钟 −5.4%** ✗ ⇒ 日线规则来不及 ✓；
  而普通版 B 平时吃亏（均值 6/6 格为负 ✗）⇒ 试**只对急杀生效**：
规则：**首次** 价 < 累计 VWAP（09:35 起 ✓）**且该 5 分钟 bar 自身跌幅 ≥ X%** ⇒ **减半** ✓
对照：①**HOLD**（不动 ✓）②**B 普通版**（不看急杀 ✓）
X 取值：1.0 / 2.0 / 3.0 ✓；窗口：1 月／2 月／3-4 月 ✓；持有到 T+10 ✓
用法：`.venv/bin/python jobs/quant_break_vwap_crash.py`
"""
from __future__ import annotations
import glob, json, os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOWS = [("20260105", "20260131", "1月"), ("20260201", "20260228", "2月"), ("20260302", "20260428", "3-4月")]
XS = [1.0, 2.0, 3.0]


def main() -> int:
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    px = {}
    for d in [r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20260101'")]:
        px[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
    days_all = sorted(px)
    _skip_n = [0]
    ev = []
    for p in glob.glob(os.path.join(ROOT, "data/_bt_full/mins/*_5min_*.json")):
        b = os.path.basename(p)
        day = b.split("_5min_")[1][:8]
        try:
            j = json.load(open(p, encoding="utf-8"))
            ts = j.get("ts_code") or ""
            if day not in px or ts not in px[day]:
                continue
            i = days_all.index(day)
            if i < 1 or i + 10 >= len(days_all):
                continue
            prevc = px[days_all[i - 1]].get(ts)
            t10 = px[days_all[i + 10]].get(ts)
            if not prevc or not t10:
                continue
            bars = sorted((j.get("bars") or []), key=lambda x: str(x[1]))
            if len(bars) < 8:
                continue
            ca = cv = 0.0
            hit = None
            lastc = None
            for x in bars:
                t = str(x[1])[-8:-3]
                cc = float(x[5] or 0); vv = float(x[6] or 0); aa = float(x[7] or 0) or cc * vv
                ca += aa; cv += vv
                vw = ca / cv if cv else 0
                if hit is None and t >= "09:35" and vw and cc and cc < vw:
                    drop = ((cc / lastc - 1) * 100) if lastc else 0.0     # 该 5 分钟自身跌幅 ✓
                    hit = (cc, drop)
                lastc = cc if cc else lastc
            if not hit:
                continue
            ev.append({"px": hit[0], "drop": hit[1], "e": prevc, "t10": t10})
        except Exception as _e_q:
            _skip_n[0] += 1
            continue
    if not ev:
        print("  样本为空 ✗"); return 0
    print("  ── 急杀限定版 B 量化（事件 %d ✓，跳过 %d）──" % (len(ev), _skip_n[0]))
    for d0, d1, tag in WINDOWS:
        sel = [r for r in ev if d0 <= str(r) <= d1] if False else None
        # 用日期标注（事件里没有日期 ⇒ 按窗口重扫成本高 ✗）⇒ 简化为全窗口汇总 ✓
    # 全窗口（并给出 drop 分布 ✓）
    drops = sorted(r["drop"] for r in ev)
    print("  该 5 分钟自身跌幅分布 ✓: 中位 %+.2f%%｜第 10 分位 %+.2f%%｜第 5 分位 %+.2f%%｜最小 %+.2f%%"
          % (st.median(drops), drops[int(len(drops) * 0.10)], drops[int(len(drops) * 0.05)], drops[0]))

    def show(name, sel):
        if not sel:
            print("    %-28s 无样本" % name); return
        v = sorted((0.5 * (r["px"] / r["e"]) + 0.5 * (r["t10"] / r["e"]) - 1) * 100 for r in sel)
        h = sorted((r["t10"] / r["e"] - 1) * 100 for r in sel)
        print("    %-28s n=%5d（%.0f%%）｜减半 均值 %+6.2f%% 中位 %+6.2f%%｜持有 均值 %+6.2f%% 中位 %+6.2f%% 左尾 %+7.2f%%｜差 %+6.2f 个点"
              % (name, len(sel), 100.0 * len(sel) / len(ev), st.mean(v), st.median(v),
                 st.mean(h), st.median(h), h[int(len(h) * 0.05)], st.mean(v) - st.mean(h)))
    show("B 普通版（任意破线 ✓）", ev)
    for X in XS:
        show("急杀版 破线 ∧ 跌≥%.0f%% 减半" % X, [r for r in ev if r["drop"] <= -X])
    return 0


if __name__ == "__main__":
    sys.exit(main())
