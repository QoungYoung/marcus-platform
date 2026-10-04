# -*- coding: utf-8 -*-
"""quant_theme_count.py —— 「多题材成员数 ⇒ 回撤/波动」量化（账本 §9.571）

**动机**（外部归因报告 §9.570 ✓）：「**多题材高弹性标的在板块降温时优先被卖**」✗
⇒ 本脚本检验：**题材数越多的票，后续回撤/波动是否越大** ✓

两个口径：
  · **概念数**：`stock_concept_map` 里该股的概念总数 ✓（301511 = 26 ✓）
  · **主题命中数**：其中属于**我们 13 个主题**的**块数** ✓（＝"多块成员" ✓，与候选机制直接相关 ✓）
前视指标（T+10 ✓）：
  · 区间收益（收盘 ✓）· **最大回撤**（最低价/入场 −1 ✓）· 日收益波动（标准差 ✓）
用法：`.venv/bin/python jobs/quant_theme_count.py [--from 20260302] [--to 20260417]`
"""
from __future__ import annotations
import argparse
import os
import sqlite3
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def theme_concepts():
    """取我们 13 个主题下的概念集合 ✓（复用 fusion_mainline.THEME_CONCEPTS ✓）"""
    for p in (os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "backend")):
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        import fusion_mainline as fm
        out = set()
        for _th, cs in (getattr(fm, "THEME_CONCEPTS", {}) or {}).items():
            for c in (cs or []):
                out.add(str(c))
        return out
    except Exception as e:
        print("  ⚠️ 取主题概念失败（%s）⇒ 退回只用概念数 ✓" % str(e)[:60])
        return set()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="d0", default="20260302")
    ap.add_argument("--to", dest="d1", default="20260417")
    a = ap.parse_args()
    dsn = os.environ.get("DATABASE_URL") or "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
    import psycopg2
    cn = psycopg2.connect(dsn, connect_timeout=10)
    cn.set_session(readonly=True, autocommit=True)
    c = cn.cursor()
    c.execute("SELECT ts_code, concept_name FROM stock_concept_map")
    conc: dict = {}
    for ts, name in c.fetchall():
        conc.setdefault(ts, set()).add(str(name))
    tc = theme_concepts()
    print("  概念表 ✓: %d 只标的｜我们的主题概念池 ✓: %d 个概念%s" % (len(conc), len(tc), "" if tc else " ✗"))
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    days = sorted(r[0] for r in db.execute(
        "SELECT DISTINCT trade_date FROM bars WHERE trade_date BETWEEN ? AND ?", (a.d0, a.d1)))
    if len(days) < 12:
        print("  交易日不足 ✗"); return 0
    px = {d: {r[0]: (float(r[1]), float(r[2])) for r in
              db.execute("SELECT ts_code, close, low FROM bars WHERE trade_date=?", (d,))} for d in days}
    # 当日全市场等权（用于"降温日"分层 ✓）
    mkt = {}
    for i in range(1, len(days)):
        d, pv = days[i], days[i - 1]
        rs = [px[d][t][0] / px[pv][t][0] - 1 for t in px[d] if t in px[pv] and px[pv][t][0] > 0]
        if rs:
            mkt[d] = st.mean(rs) * 100
    rows = []
    for i in range(1, len(days) - 10):
        d = days[i]
        for ts, (cl, lo) in px[d].items():
            cs = conc.get(ts)
            if not cs or cl <= 0:
                continue
            n_all = len(cs)
            n_theme = len([x for x in cs if x in tc]) if tc else 0
            fwd = []
            lows = []
            for k in range(1, 11):
                p2 = px[days[i + k]].get(ts)
                if not p2:
                    break
                fwd.append(p2[0] / cl - 1)
                lows.append(p2[1] / cl - 1)
            if len(fwd) < 10:
                continue
            ret = fwd[-1] * 100
            mdd = min(lows) * 100
            vol = st.pstdev(fwd) * 100
            rows.append((n_all, n_theme, ret, mdd, vol))
    if not rows:
        print("  样本为空 ✗"); return 0
    print("  样本 ✓: %d 个 (标的,日)｜窗口 %s~%s（T+10 ✓）" % (len(rows), days[0], days[-1]))
    # ★ 降温日分层（报告的原始主张：**板块降温时**多题材是否更惨 ✓）
    cool = [r for r, dd in zip(rows, [days[i] for i in range(0, 0)])] if False else None
    for thr, lab in ((0.0, "全市场等权 < 0（降温日 ✓）"), (-1.0, "全市场等权 < -1%（明显降温 ✓）")):
        sel_all = []
        for i in range(1, len(days) - 10):
            d = days[i]
            if mkt.get(d, 0.0) >= thr:
                continue
            for ts, (cl, lo) in px[d].items():
                cs = conc.get(ts)
                if not cs or cl <= 0:
                    continue
                fwd, lows = [], []
                for k in range(1, 11):
                    p2 = px[days[i + k]].get(ts)
                    if not p2:
                        break
                    fwd.append(p2[0] / cl - 1)
                    lows.append(p2[1] / cl - 1)
                if len(fwd) < 10:
                    continue
                sel_all.append((len(cs), len([x for x in cs if x in tc]) if tc else 0,
                                fwd[-1] * 100, min(lows) * 100, st.pstdev(fwd) * 100))
        if len(sel_all) < 100:
            continue
        print("  ════ %s（样本 %d ✓）════" % (lab, len(sel_all)))
        for lo_, hi_, glab in [(0, 8, "概念 1~8"), (9, 15, "概念 9~15"), (16, 25, "概念 16~25"), (26, 9999, "概念 26+")]:
            sel = [r for r in sel_all if lo_ <= r[0] <= hi_]
            if len(sel) < 30:
                continue
            print("     %-12s n=%6d｜T+10 %+6.2f%%｜回撤 %+6.2f%%｜波动 %5.2f%%"
                  % (glab, len(sel), st.mean(r[2] for r in sel), st.mean(r[3] for r in sel), st.mean(r[4] for r in sel)))

    def bucket(name, key, edges):
        print("  ── %s ──" % name)
        print("     %-14s %8s %10s %10s %10s" % ("分组", "样本", "T+10 收益", "最大回撤", "波动"))
        for lo, hi, lab in edges:
            sel = [r for r in rows if lo <= r[key] <= hi]
            if len(sel) < 30:
                continue
            print("     %-14s %8d %+9.2f%% %+9.2f%% %9.2f%%"
                  % (lab, len(sel), st.mean(r[2] for r in sel),
                     st.mean(r[3] for r in sel), st.mean(r[4] for r in sel)))
    bucket("按 **概念总数** ✓", 0, [(0, 3, "1~3"), (4, 8, "4~8"), (9, 15, "9~15"),
                                    (16, 25, "16~25"), (26, 9999, "26+")])
    if tc:
        bucket("按 **命中我们的主题块数** ✓", 1, [(0, 0, "0"), (1, 1, "1"), (2, 3, "2~3"),
                                                  (4, 6, "4~6"), (7, 999, "7+")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
