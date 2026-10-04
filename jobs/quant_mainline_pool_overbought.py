# -*- coding: utf-8 -*-
"""quant_mainline_pool_overbought.py —— **主线上"会看的票"**里的超买/乖离量化（账本 §9.576）

**背景**：§9.575 只在**真候选池**里做 ✓，但候选文件只覆盖 13 天 ✗（t35 臂停在 0318 ✓）⇒ 样本小 ✗
**本脚本**：用**可全覆盖**的数据重建近似池 ✓：
  · 当日**主线方向**：`data/_bt_t35/<日>/wolf_mainline_select.json` 的 **r5 前 3** ✓（42 天全覆盖 ✓）
  · 该方向下的**概念成员**：`fusion_mainline.THEME_CONCEPTS`（主题→概念 ✓）→ PG `stock_concept_map` ✓
  · ⇒ 池子 ＝ **当天主线前三方向的概念成员** ✓（≈"主线上会看的票" ✓，比真候选池宽 ✓）
度量：入场日 RSI14／乖离 MA20 ✓ ⇒ **T+1／T+2／T+3** ✓（均值／左尾 ✓）
用法：`.venv/bin/python jobs/quant_mainline_pool_overbought.py [--topk 3]`
"""
from __future__ import annotations
import argparse, glob, json, os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def theme_concepts():
    for p in (os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, "backend")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import fusion_mainline as fm
    out = {}
    for th, cs in (getattr(fm, "THEME_CONCEPTS", {}) or {}).items():
        out[str(th)] = [str(c) for c in (cs or [])]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topk", type=int, default=3)
    a = ap.parse_args()
    tc = theme_concepts()
    if not tc:
        print("  ✗ 取不到主题概念表"); return 1
    import psycopg2
    cn = psycopg2.connect(os.environ.get("DATABASE_URL") or
                          "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading", connect_timeout=10)
    cn.set_session(readonly=True, autocommit=True)
    c = cn.cursor()
    c.execute("SELECT concept_name, ts_code FROM stock_concept_map")
    byc: dict = {}
    for cn_, ts in c.fetchall():
        byc.setdefault(str(cn_), set()).add(str(ts))
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    days = sorted(r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20260101'"))
    cl = {}
    for d in days:
        cl[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
    idx = {d: i for i, d in enumerate(days)}
    rows = []
    used = 0
    for f in sorted(glob.glob(os.path.join(ROOT, "data", "_bt_t35", "2026*", "wolf_mainline_select.json"))):
        day = os.path.basename(os.path.dirname(f))
        if day not in idx:
            continue
        i = idx[day]
        if i < 25 or i + 3 >= len(days):
            continue
        try:
            j = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        r5 = j.get("r5") or {}
        tops = [k for k, _v in sorted(r5.items(), key=lambda kv: -float(kv[1] or 0))[:a.topk]]
        pool = set()
        for th in tops:
            for cn_ in tc.get(th, []):
                pool |= byc.get(cn_, set())
        if not pool:
            continue
        used += 1
        for ts in pool:
            cpx = cl[day].get(ts)
            if not cpx or cpx <= 0:
                continue
            c15 = [cl[days[i - k]].get(ts) for k in range(0, 15)]
            c20 = [cl[days[i - k]].get(ts) for k in range(0, 21)]
            if any(x is None for x in c15) or any(x is None for x in c20):
                continue
            seq = list(reversed(c15))
            g = l = 0.0
            for k in range(1, len(seq)):
                ch = seq[k] - seq[k - 1]
                g += max(ch, 0.0); l += max(-ch, 0.0)
            rsi = 100.0 * g / (g + l) if (g + l) else 50.0
            bias = (cpx / st.mean(c20) - 1) * 100
            fw = {}
            ok = True
            for k in (1, 2, 3):
                p = cl[days[i + k]].get(ts)
                if not p:
                    ok = False; break
                fw[k] = (p / cpx - 1) * 100
            if not ok:
                continue
            rows.append({"day": day, "sym": ts, "rsi": rsi, "bias": bias, "r1": fw[1], "r2": fw[2], "r3": fw[3]})
    print("  ── 主线上会看的票（r5 前 %d 方向的概念成员 ✓）──" % a.topk)
    print("     覆盖 ✓: %d 天｜样本 ✓: %d 个（标的,日）｜标的 ✓: %d 只" % (used, len(rows), len({r["sym"] for r in rows})))
    if not rows:
        return 0

    def rep(name, key, buckets):
        print("  ── %s ──" % name)
        print("     %-14s %7s %9s %9s %9s %11s" % ("分组", "占比", "T+1", "T+2", "T+3", "**T+3 左尾5%**"))
        for lo, hi, lab in buckets:
            sel = [r for r in rows if lo <= r[key] < hi]
            if len(sel) < 30:
                continue
            v3 = sorted(r["r3"] for r in sel)
            print("     %-14s %6.1f%% %+8.2f%% %+8.2f%% %+8.2f%% %10.2f%%"
                  % (lab, 100.0 * len(sel) / len(rows),
                     st.mean(r["r1"] for r in sel), st.mean(r["r2"] for r in sel),
                     st.mean(r["r3"] for r in sel), v3[int(len(v3) * 0.05)]))
    rep("① RSI14（超买 ✓）", "rsi", [(0, 60, "RSI<60"), (60, 70, "60~70"), (70, 80, "**70~80**"), (80, 999, "**80+ 超买**")])
    rep("② 乖离 MA20 ✓", "bias", [(-999, 0, "乖离<0"), (0, 10, "0~10%"), (10, 20, "**10~20%**"), (20, 999, "**20%+**")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
