# -*- coding: utf-8 -*-
"""quant_entry_pool_overbought.py —— **只在"我们会买入的票"里**看超买/乖离的影响（账本 §9.575）

**用户口述（2026-10-04）**：「你应该找**我们会买入的票**，然后看这些票的超买和乖离率对后续收益的影响」✓
⇒ ⇒ 此前 §9.572／§9.574 是**全市场横截面**（测的是 Beta ✗）⇒ 本脚本改为**条件在候选池内** ✓

**"我们会买入的票"定义**（取并集 ✓）：
  · 当日**候选池**：`data/_bt_t35/<日>/stock_confirm_result.json` 各块的 `stocks` ✓
  · 当日**已布腿**：`data/_bt_t35/<日>/legs_switch.jsonl` 的 symbol ✓
度量：入场日算 RSI14／乖离 MA20 ✓ ⇒ 看 **T+1／T+2／T+3** 收益（均值／左尾 ✓）
用法：`.venv/bin/python jobs/quant_entry_pool_overbought.py`
"""
from __future__ import annotations
import glob, json, os, sqlite3, statistics as st, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def pool_for(day: str) -> set:
    out = set()
    base = os.path.join(ROOT, "data", "_bt_t35", day)
    p = os.path.join(base, "stock_confirm_result.json")
    if os.path.exists(p):
        try:
            j = json.load(open(p, encoding="utf-8"))
            for _b, v in (j.items() if isinstance(j, dict) else []):
                if isinstance(v, dict):
                    for s in (v.get("stocks") or []):
                        cd = s.get("code") if isinstance(s, dict) else s
                        if cd: out.add(str(cd))
        except Exception as _e_p1:
            print("[pool] 候选文件读取失败 %s: %s" % (p, str(_e_p1)[:60]))
    p2 = os.path.join(base, "legs_switch.jsonl")
    if os.path.exists(p2):
        try:
            for line in open(p2, encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                o = json.loads(line)
                cd = o.get("symbol") or o.get("code")
                if cd: out.add(str(cd))
        except Exception as _e_p2:
            print("[pool] 布腿文件读取失败 %s: %s" % (p2, str(_e_p2)[:60]))
    return out


def main() -> int:
    db = sqlite3.connect("file:%s/data/_bt_full/bars.sqlite?mode=ro" % ROOT, uri=True)
    db.execute("PRAGMA temp_store=MEMORY")
    days = sorted(r[0] for r in db.execute("SELECT DISTINCT trade_date FROM bars WHERE trade_date >= '20260101'"))
    cl = {}
    for d in days:
        cl[d] = {r[0]: float(r[1]) for r in db.execute("SELECT ts_code, close FROM bars WHERE trade_date=?", (d,))}
    idx = {d: i for i, d in enumerate(days)}
    day_dirs = [os.path.basename(p.rstrip("/")) for p in glob.glob(os.path.join(ROOT, "data", "_bt_t35", "2026*"))]
    _skip_n = [0]
    rows = []
    for day in sorted(set(day_dirs)):
        if day not in idx:
            continue
        i = idx[day]
        if i < 25 or i + 3 >= len(days):
            continue
        for ts in pool_for(day):
            tss = ts if "." in ts else (ts + (".SZ" if ts[:1] in ("0", "3") else ".SH"))
            c = cl[day].get(tss)
            if not c or c <= 0:
                continue
            c15 = [cl[days[i - k]].get(tss) for k in range(0, 15)]
            c20 = [cl[days[i - k]].get(tss) for k in range(0, 21)]
            if any(x is None for x in c15) or any(x is None for x in c20):
                continue
            seq = list(reversed(c15))
            g = l = 0.0
            for k in range(1, len(seq)):
                ch = seq[k] - seq[k - 1]
                g += max(ch, 0.0); l += max(-ch, 0.0)
            rsi = 100.0 * g / (g + l) if (g + l) else 50.0
            bias = (c / st.mean(c20) - 1) * 100
            fw = {}
            ok = True
            for k in (1, 2, 3):
                p = cl[days[i + k]].get(tss)
                if not p:
                    ok = False; break
                fw[k] = (p / c - 1) * 100
            if not ok:
                continue
            rows.append({"day": day, "sym": tss, "rsi": rsi, "bias": bias,
                         "r1": fw[1], "r2": fw[2], "r3": fw[3]})
    print("  -- 我们会买入的票（候选池 併 已布腿）样本: %d 个（标的,日）--" % len(rows))
    if not rows:
        return 0
    print("     覆盖交易日 ✓: %d 天｜标的 ✓: %d 只" % (len({r["day"] for r in rows}), len({r["sym"] for r in rows})))

    def rep(name, key, buckets):
        print("  ── %s ──" % name)
        print("     %-14s %7s %9s %9s %9s %11s" % ("分组", "占比", "T+1", "T+2", "T+3", "**T+3 左尾5%**"))
        for lo, hi, lab in buckets:
            sel = [r for r in rows if lo <= r[key] < hi]
            if len(sel) < 15:
                continue
            v3 = sorted(r["r3"] for r in sel)
            print("     %-14s %6.1f%% %+8.2f%% %+8.2f%% %+8.2f%% %10.2f%%"
                  % (lab, 100.0 * len(sel) / len(rows),
                     st.mean(r["r1"] for r in sel), st.mean(r["r2"] for r in sel),
                     st.mean(r["r3"] for r in sel), v3[int(len(v3) * 0.05)]))
    rep("① RSI14（超买 ✓）", "rsi", [(0, 60, "RSI<60"), (60, 70, "60~70"),
                                     (70, 80, "**70~80**"), (80, 999, "**80+ 超买**")])
    rep("② 乖离 MA20 ✓", "bias", [(-999, 0, "乖离<0"), (0, 10, "0~10%"),
                                   (10, 20, "**10~20%**"), (20, 999, "**20%+**")])
    return 0


if __name__ == "__main__":
    sys.exit(main())
