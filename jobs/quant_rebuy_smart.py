# -*- coding: utf-8 -*-
"""quant_rebuy_smart.py —— 减半之后**能不能补回来**：三种"回补"口径量化（账本 §9.558）

背景：B（破分时黄线 ⇒ 减半 ✓）落地后**不带回补** ✗ ⇒ 问题是"减掉的半仓还能不能拿回来" ✓
口径（都以**前收**为基准 ✓，复利因子 ✓，到 T+10 ✓）：
  · **B**      破线减半，**不回补**（§9.557 基线 ✓）
  · **R1 VWAP回归** 减半后，**当日**若收盘价 **回到 VWAP 上方** ⇒ 补回 ✓
  · **R3 认错回补**  减半后，**次日收盘 > 卖出价** ⇒ 补回（承认卖错 ✓）
  · **R4 次日站上MA5** 减半后，**次日收盘 > 5日均线** ⇒ 补回 ✓
用法：`.venv/bin/python jobs/quant_rebuy_smart.py [--from D --to D]`
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
    px, ma5 = {}, {}
    for day in [r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ?",
                                         (d0, d1))]:
        px[day] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (day,))}
    days = sorted(px)
    for i, day in enumerate(days):
        if i < 5:
            continue
        for ts in px[day]:
            v = [px[days[i - k]].get(ts) for k in range(5)]
            if all(v):
                ma5.setdefault(day, {})[ts] = sum(v) / 5.0
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
            if i == 0:
                continue
            prevc = px[days[i - 1]].get(ts)
            if not prevc:
                continue
            bars = sorted((j.get("bars") or []), key=lambda x: str(x[1]))
            if len(bars) < 8:
                continue
            ca = cv = 0.0
            hit = None
            vwap_close = None
            for x in bars:
                t = str(x[1])[-8:-3]
                cc = float(x[5] or 0); vv = float(x[6] or 0); aa = float(x[7] or 0) or cc * vv
                ca += aa; cv += vv
                vw = ca / cv if cv else 0
                if hit is None and t >= "09:35" and vw and cc and cc < vw:
                    hit = (t, cc)
            if not hit:
                continue
            vwap_close = (ca / cv) if cv else 0
            close_day = px[day].get(ts)
            nxt = lambda k: (px[days[i + k]].get(ts) if i + k < len(days) else None)
            r10 = ((nxt(10) / prevc - 1) * 100) if nxt(10) else None
            if r10 is None:
                continue
            sell = (hit[1] / prevc - 1) * 100
            rb1 = None
            if close_day and vwap_close and close_day >= vwap_close:      # R1 当日收回 VWAP 上方 ✓
                rb1 = (close_day / prevc - 1) * 100
            rb3 = None
            if nxt(1) and nxt(1) > hit[1]:                                # R3 次日收盘 > 卖价 ✓
                rb3 = (nxt(1) / prevc - 1) * 100
            rb4 = None
            if nxt(1) and ma5.get(days[i + 1], {}).get(ts) and nxt(1) > ma5[days[i + 1]][ts]:
                rb4 = (nxt(1) / prevc - 1) * 100                          # R4 次日站上 MA5 ✓
            ev.append({"sell": sell, "r10": r10, "rb1": rb1, "rb3": rb3, "rb4": rb4})
        except Exception as _e_q:
            _skip_n[0] += 1
            continue
    if not ev:
        print("  样本为空 ✗"); return 0
    f = lambda x: 1.0 + x / 100.0
    print("  -- 减半之后能否补回来 -- %s ~ %s (到 T+10)" % (d0, d1))
    print("  事件 ✓: %d｜跳过 %d｜可供回补的比例 ✓: R1 %.0f%%｜R3 %.0f%%｜R4 %.0f%%"
          % (len(ev), _skip_n[0], 100.0 * sum(1 for e in ev if e["rb1"] is not None) / len(ev),
             100.0 * sum(1 for e in ev if e["rb3"] is not None) / len(ev),
             100.0 * sum(1 for e in ev if e["rb4"] is not None) / len(ev)))

    def show(name, fn):
        v = sorted(x for x in (fn(e) for e in ev) if x is not None)
        if not v:
            print("    %-24s 无样本" % name); return
        print("    %-24s n=%5d｜均值 %+6.2f%%｜中位 %+6.2f%%｜左尾 %+7.2f%%"
              % (name, len(v), st.mean(v), st.median(v), v[int(len(v) * 0.05)]))
    show("B 减半（不回补）", lambda e: (0.5 * f(e["sell"]) + 0.5 * f(e["r10"]) - 1) * 100)
    def mk(key):
        def g(e):
            if e[key] is None:
                return (0.5 * f(e["sell"]) + 0.5 * f(e["r10"]) - 1) * 100     # 未回补 ⇒ 同 B ✓
            return (0.5 * f(e["sell"]) + 0.5 * f(e["sell"]) * f(e["r10"]) / f(e[key]) - 1) * 100
        return g
    show("R1 当日收回VWAP⇒补回", mk("rb1"))
    show("R3 次日>卖价⇒补回", mk("rb3"))
    show("R4 次日站上MA5⇒补回", mk("rb4"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
