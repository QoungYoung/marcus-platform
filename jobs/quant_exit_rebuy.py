# -*- coding: utf-8 -*-
"""quant_exit_rebuy.py —— 破分时黄线:**补"持有"基线 ＋ "卖后回补"**（账本 §9.557）

四组对照（同一批破线事件 ✓）：
  · **HOLD**  不动，持有到 T+5／T+10 ✓（**基线** ✓）
  · **R0**    破线**立即全卖** ✓
  · **R0B**   破线全卖 ⇒ **当日 14:00–14:30 回补**（若回补价 < 卖价 ✓；否则不回补 ✓）⇒ 再到 T+5／T+10 ✓
  · **B**     破线**减半**（卖一半 ✓）持有到 T+5／T+10 ✓
  · **BB**    减半 ＋ **另一半当日回补**（＝实质满仓 ✓，作为对照 ✓）
语料依据：他 2025-04-15 条件6「想追进去的…**在下午 2.00-2.30 回补**」✓
用法：`.venv/bin/python jobs/quant_exit_rebuy.py [--from D --to D]`
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
        px[day] = {r[0]: float(r[1]) for r in
                   db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (day,))}
    days = sorted(px)
    _skip_n = [0]
    ev = []
    for p in glob.glob(os.path.join(ROOT, "data/_bt_full/mins/*_5min_*.json")):
        b = os.path.basename(p)
        day = b.split("_5min_")[1][:8]
        if not (d0 <= day <= d1 or True):
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
            bars = j.get("bars") or []
            if len(bars) < 8:
                continue
            seq = sorted(bars, key=lambda x: str(x[1]))
            ca = cv = 0.0
            hit = None
            rebuy = None
            for x in seq:
                t = str(x[1])[-8:-3]
                cc = float(x[5] or 0); vv = float(x[6] or 0); aa = float(x[7] or 0) or cc * vv
                ca += aa; cv += vv
                vw = ca / cv if cv else 0
                if hit is None and t >= "09:35" and vw and cc and cc < vw:
                    hit = (t, cc)
                if hit is not None and "14:00" <= t <= "14:30" and cc > 0:
                    rebuy = cc          # 14:00-14:30 的第一根（约 14:00）✓
            if not hit:
                continue
            nxt = lambda k: (px[days[i + k]].get(ts) if i + k < len(days) else None)
            hold = lambda k: ((nxt(k) / prevc - 1) * 100 if nxt(k) else None)
            sell = (hit[1] / prevc - 1) * 100
            rb = None
            if rebuy:
                rb = (rebuy / prevc - 1) * 100      # 回补价 相对前收 ✓
                if rebuy >= hit[1]:
                    rb = None                        # 回补价不低于卖价 ⇒ 不回补 ✓
            ev.append({"sell": sell, "rb": rb, "h5": hold(5), "h10": hold(10)})
        except Exception as _e_q:
            _skip_n[0] += 1
            continue
    if not ev:
        print("  样本为空 ✗"); return 0
    print("  ── 破线事件:持有基线 ＋ 卖后回补（%s ~ %s ✓）──" % (d0, d1))
    print("  事件数 ✓: %d｜其中满足「回补价<卖价」 ✓: %d（%.0f%%）"
          % (len(ev), sum(1 for e in ev if e["rb"] is not None),
             100.0 * sum(1 for e in ev if e["rb"] is not None) / len(ev)))

    def show(name, fn):
        v5 = sorted(x for x in (fn(e, 5) for e in ev) if x is not None)
        v10 = sorted(x for x in (fn(e, 10) for e in ev) if x is not None)
        if not v5:
            print("    %-22s 无样本" % name); return
        print("    %-22s T+5 均值 %+6.2f%% 中位 %+6.2f%% 左尾 %+7.2f%%｜T+10 均值 %+6.2f%% 中位 %+6.2f%% 左尾 %+7.2f%%"
              % (name, st.mean(v5), st.median(v5), v5[int(len(v5) * 0.05)],
                 st.mean(v10), st.median(v10), v10[int(len(v10) * 0.05)]))
    def f(x):          # 收益% ⇒ 复利因子 ✓
        return 1.0 + (x or 0.0) / 100.0

    def as_pct(fac):
        return (fac - 1.0) * 100.0

    def hold_fac(e, k):
        return f(e["h%d" % k]) if e["h%d" % k] is not None else None

    def show2(name, fn):
        v5 = sorted(as_pct(x) for x in (fn(e, 5) for e in ev) if x is not None)
        v10 = sorted(as_pct(x) for x in (fn(e, 10) for e in ev) if x is not None)
        if not v5:
            print("    %-22s 无样本" % name); return
        print("    %-22s T+5 均值 %+6.2f%% 中位 %+6.2f%% 左尾 %+7.2f%%｜T+10 均值 %+6.2f%% 中位 %+6.2f%% 左尾 %+7.2f%%"
              % (name, st.mean(v5), st.median(v5), v5[int(len(v5) * 0.05)],
                 st.mean(v10), st.median(v10), v10[int(len(v10) * 0.05)]))

    show2("HOLD 持有", lambda e, k: hold_fac(e, k))
    show2("R0 破线全卖", lambda e, k: f(e["sell"]))
    # 全卖 ⇒ 当日 14:00-14:30 回补（回补价低于卖价时 ✓）⇒ 再持有到 T+k ✓（复利 ✓）
    show2("R0B 全卖+回补", lambda e, k: (f(e["sell"]) * hold_fac(e, k) / f(e["rb"]))
         if (e["rb"] is not None and hold_fac(e, k) is not None) else f(e["sell"]))
    # 破线减半（另一半不动 ✓）
    show2("B 破线减半", lambda e, k: (0.5 * f(e["sell"]) + 0.5 * hold_fac(e, k))
         if hold_fac(e, k) is not None else None)
    # 减半 + 另一半当日回补（＝实质满仓 ✓）
    show2("BB 减半+回补", lambda e, k: (0.5 * f(e["sell"]) + 0.5 * f(e["sell"]) * hold_fac(e, k) / f(e["rb"]))
         if (e["rb"] is not None and hold_fac(e, k) is not None) else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
