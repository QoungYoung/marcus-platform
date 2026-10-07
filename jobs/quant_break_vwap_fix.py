# -*- coding: utf-8 -*-
"""quant_break_vwap_fix.py —— B(破线减半) 的**口径修正**量化（账本 §9.559）

背景（§9.558 实测发现的缺陷 ✗）：低吸买入的价**本来就在黄线下** ⇒ 现行口径会**买入即减半** ✗
候选修法：
  · **B0**（现行）任意破线 ⇒ 减半 ✓（基线）
  · **B1** **持仓 ≥1 日**才允许减半 ✓（＝破线发生在入场后第 2 日起 ✓）
  · **B2** 只对**已亏损**的仓减半 ✓（破线价 < 入场价 ✓）
做法：以 (标的, 日) 的破线事件为基础 ✓，把**入场价**取为 D−k 日收盘（k=1/2/3 ✓）⇒
  k=1 ⇒ "买入即破线"（B0 样本 ✓）；k≥2 ⇒ 已持仓 ≥1 日 ✓；再按"破线价 vs 入场价"分亏损/盈利 ✓
输出：各口径的触发率、均值/中位/左尾（到 T+10 ✓）
用法：`.venv/bin/python jobs/quant_break_vwap_fix.py [--from D --to D]`
"""
from __future__ import annotations
import glob, json, os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    d0, d1 = "20260302", "20260428"
    for i, a in enumerate(sys.argv):
        if a == "--from" and i + 1 < len(sys.argv): d0 = sys.argv[i + 1]
        if a == "--to" and i + 1 < len(sys.argv): d1 = sys.argv[i + 1]
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    px = {}
    for day in [r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ?",
                                         (d0, d1))]:
        px[day] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (day,))}
    days = sorted(px)
    _skip_n = [0]
    ev = []
    for p in glob.glob(os.path.join(ROOT, "data/_bt_full/mins/*_5min_*.json")):
        b = os.path.basename(p)
        day = b.split("_5min_")[1][:8]
        if not (d0 <= day <= d1):
            continue
        try:
            j = json.load(open(p, encoding="utf-8"))
            ts = j.get("ts_code") or ""
            if day not in px or ts not in px[day]:
                continue
            i = days.index(day)
            if i < 3:
                continue
            bars = sorted((j.get("bars") or []), key=lambda x: str(x[1]))
            if len(bars) < 8:
                continue
            ca = cv = 0.0
            hit = None
            for x in bars:
                t = str(x[1])[-8:-3]
                cc = float(x[5] or 0); vv = float(x[6] or 0); aa = float(x[7] or 0) or cc * vv
                ca += aa; cv += vv
                vw = ca / cv if cv else 0
                if hit is None and t >= "09:35" and vw and cc and cc < vw:
                    hit = (t, cc)
            if not hit:
                continue
            t10 = px[days[i + 10]].get(ts) if i + 10 < len(days) else None
            if t10 is None:
                continue
            rec = {"px": hit[1], "t10": t10}
            for k in (1, 2, 3):
                e = px[days[i - k]].get(ts)
                rec["e%d" % k] = e
            ev.append(rec)
        except Exception as _e_q:
            _skip_n[0] += 1
            continue
    if not ev:
        print("  样本为空 ✗"); return 0
    print("  ── B 口径修正量化（%s ~ %s ✓，到 T+10 ✓）──" % (d0, d1))
    print("  破线事件 ✓: %d｜跳过 %d" % (len(ev), _skip_n[0]))

    def half(fac_sell, fac_hold):
        return (0.5 * fac_sell + 0.5 * fac_hold - 1.0) * 100.0

    def show(name, sel):
        v = sorted(half(r["px"] / r["e"] , r["t10"] / r["e"]) for r in sel)
        if not v:
            print("    %-30s 无样本" % name); return
        print("    %-30s n=%5d（触发率 %4.0f%%）｜均值 %+6.2f%%｜中位 %+6.2f%%｜左尾 %+7.2f%%"
              % (name, len(v), 100.0 * len(v) / len(ev), st.mean(v), st.median(v), v[int(len(v) * 0.05)]))
    for k, kk in ((1, "k=1 买入即破线"), (2, "k=2 持仓≥1日"), (3, "k=3 持仓≥2日")):
        sel = [dict(r, e=r["e%d" % k]) for r in ev if r.get("e%d" % k)]
        show("B0 现行（%s）" % kk, sel)
        show("B2 只做亏损（%s）" % kk, [r for r in sel if r["px"] < r["e"]])
        show("HOLD 持有（%s 对照）" % kk, sel) if False else None
    # 对照：持仓（不因破线减半）
    sel = [dict(r, e=r["e2"]) for r in ev if r.get("e2")]
    v = sorted((r["t10"] / r["e"] - 1) * 100 for r in sel)
    print("    %-30s n=%5d｜均值 %+6.2f%%｜中位 %+6.2f%%｜左尾 %+7.2f%%"
          % ("HOLD 持有（k=2 对照）", len(v), st.mean(v), st.median(v), v[int(len(v) * 0.05)]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
