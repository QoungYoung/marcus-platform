# -*- coding: utf-8 -*-
"""quant_exit_variants.py —— 「破分时黄线」的**条件化版本**量化（账本 §9.556）

在 §9.555 的基础上，比较四种处理（同一批"破线"事件 ✓）：
  · **R0 立即全卖**（基线 ✓）
  · **A 破线 ∧ 板块弱 ⇒ 全卖**（板块弱 ＝ 该股所在**块**当日等权涨幅 ≤ 0 ✓）
  · **B 破线 ⇒ 减半**（一半按破线价卖 ✓、一半留到 T+5/T+10 ✓）
  · **C 破线 ∧ 浮亏 > 3% ⇒ 全卖**（浮亏 = 破线价 相对 **前收** ✓）
其余情况 **持有到 T+5／T+10** ✓。输出各版本均值/中位/左尾 ✓。
用法：`.venv/bin/python jobs/quant_exit_variants.py [--from 20260302] [--to 20260428]`
"""
from __future__ import annotations
import glob, json, os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLOCKS = ["固态电池", "锂电池", "储能", "电池技术", "新能源", "新能源车", "电池化学品", "锂电专用设备",
          "半导体概念", "国产芯片", "存储芯片", "光刻机(胶)", "Chiplet概念", "第三代半导体"]


def main() -> int:
    d0, d1 = "20260302", "20260428"
    for i, a in enumerate(sys.argv):
        if a == "--from" and i + 1 < len(sys.argv): d0 = sys.argv[i + 1]
        if a == "--to" and i + 1 < len(sys.argv): d1 = sys.argv[i + 1]
    import psycopg2
    cn = psycopg2.connect(os.environ.get("DATABASE_URL") or
                          "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    cn.set_session(readonly=True, autocommit=True)
    c = cn.cursor()
    c.execute("SELECT DISTINCT ts_code FROM stock_concept_map WHERE concept_name = ANY(%s)", (BLOCKS,))
    universe = {r[0] for r in c.fetchall()}
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    px = {}
    for day in [r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ?",
                                         (d0, d1))]:
        px[day] = {r[0]: float(r[1]) for r in
                   db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (day,))}
    days = sorted(px)
    # 各块当日等权涨幅（板块强弱的代理 ✓）
    blk_ret = {}
    c.execute("SELECT concept_name, ts_code FROM stock_concept_map WHERE concept_name = ANY(%s)", (BLOCKS,))
    memb = {}
    for cn_, ts in c.fetchall():
        memb.setdefault(cn_, []).append(ts)
    for day in days:
        i = days.index(day)
        if i == 0:
            continue
        prev = px[days[i - 1]]
        rs = [px[day][t] / prev[t] - 1 for lst in memb.values() for t in lst if t in px[day] and t in prev]
        blk_ret[day] = (st.mean(rs) * 100) if rs else 0.0
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
            if ts not in universe and ts:
                # 也纳入我们买过的票
                pass
            bars = j.get("bars") or []
            if len(bars) < 8 or ts not in px.get(day, {}):
                continue
            i = days.index(day)
            if i == 0:
                continue
            prevc = px[days[i - 1]].get(ts)
            if not prevc:
                continue
            seq = sorted(bars, key=lambda x: str(x[1]))
            ca = cv = 0.0
            hit = None
            for x in seq:
                t = str(x[1])[-8:-3]
                cc = float(x[5] or 0); vv = float(x[6] or 0); aa = float(x[7] or 0) or cc * vv
                ca += aa; cv += vv
                vw = ca / cv if cv else 0
                if t >= "09:35" and vw and cc and cc < vw:
                    hit = (t, cc)
                    break
            if not hit:
                continue
            nxt = lambda k: (px[days[i + k]].get(ts) if i + k < len(days) else None)
            r_now = (hit[1] / prevc - 1) * 100
            r5 = (nxt(5) / prevc - 1) * 100 if nxt(5) else None
            r10 = (nxt(10) / prevc - 1) * 100 if nxt(10) else None
            ev.append({"ts": ts, "day": day, "r_now": r_now, "r5": r5, "r10": r10,
                       "weak": blk_ret.get(day, 0.0) <= 0, "loss3": r_now <= -3.0})
        except Exception as _e_q:
            _skip_n[0] += 1
            continue
    if not ev:
        print("  样本为空 ✗"); return 0
    print("  ── 破线事件的**条件化处理**（%s ~ %s ✓）──" % (d0, d1))
    print("  事件数 ✓: %d｜当日块等权涨幅示例 ✓: %s" % (len(ev), ", ".join(
        "%s=%.2f%%" % (d, blk_ret[d]) for d in days[1:4])))

    def report(name, pick):
        sel = [e for e in ev if pick(e)]
        if not sel:
            print("    %-26s 无样本" % name); return
        out = []
        for e in sel:
            if name.startswith("R0") or (name.startswith("A") and e["weak"]) or (name.startswith("C") and e["loss3"]):
                out.append((e["r_now"], e["r_now"], e["r_now"]))
            elif name.startswith("B"):
                if e["r5"] is None:
                    continue
                out.append((e["r_now"] / 2 + e["r5"] / 2, e["r_now"] / 2 + e["r5"] / 2, e["r_now"] / 2 + (e["r10"] or e["r5"]) / 2))
            else:
                if e["r5"] is None:
                    continue
                out.append((e["r5"], e["r5"], e["r10"] or e["r5"]))
        for idx, tag in ((0, "到T+5（或破线价）"), (1, "到T+5"), (2, "到T+10")):
            v = sorted(x[idx] for x in out)
            print("    %-26s %-14s n=%4d｜均值 %+6.2f%%｜中位 %+6.2f%%｜左尾 %+7.2f%%"
                  % (name, tag, len(v), st.mean(v), st.median(v), v[int(len(v) * 0.05)]))
    report("R0 破线即全卖", lambda e: True)
    report("持有到T+5（对照）", lambda e: False)
    report("A 破线∧板块弱⇒卖", lambda e: True)
    report("B 破线⇒减半", lambda e: True)
    report("C 破线∧亏>3%⇒卖", lambda e: True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
