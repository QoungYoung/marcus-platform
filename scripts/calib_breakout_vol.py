# -*- coding: utf-8 -*-
"""calib_breakout_vol.py — 标定**突破腿的量比门槛**（§9.853，用户「B 先通、A 同时做」✓）

【为什么 ✓】`WOLF_BREAKOUT_VOL_MIN=1.0` 是**我们自设的代理值** ✗（语料只说「带量突破」✓、无数字 ✗），
  实测它把真实突破（量比 0.89~0.91 ✗）全判成"不带量"⇒ 突破腿**一笔不成交** ✗。
【做法 ✓】直接用**触发快照里的量比**（`t_triggers.snapshot.vol_ratio` ✓ 盘中归一口径 ✓）
  × 复权序列的 T+5/T+10 ⇒ 按量比分档统计 ✓ ⇒ 用数据定门槛 ✓（而不是拍脑袋 ✗）
用法：python3 scripts/calib_breakout_vol.py [--db data/_bt_full/bars_adj.sqlite]
"""
from __future__ import annotations
import argparse, collections, os, re, sqlite3, statistics

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/_bt_full/bars_adj.sqlite")
    ap.add_argument("--dsn", default="postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    a = ap.parse_args()
    import psycopg2
    cn = psycopg2.connect(a.dsn); cur = cn.cursor()
    cur.execute("""
        select symbol, created_at::date::text,
               coalesce((snapshot->'fields'->>'vol_ratio')::float, 0),
               coalesce(event_type,''), coalesce(status,'')
        from t_triggers
        where event_type like '%trend_break%'
          and coalesce((snapshot->'fields'->>'vol_ratio')::float, 0) > 0
        order by created_at""")
    rows = cur.fetchall(); cn.close()
    print("  突破腿触发（带量比快照 ✓）= %d 条" % len(rows))
    S = collections.defaultdict(lambda: {"d": [], "c": []})
    c2 = sqlite3.connect(a.db); cu = c2.cursor()
    for ts, d, c in cu.execute("select ts_code, trade_date, close from bars order by ts_code, trade_date"):
        try:
            S[ts]["d"].append(str(d).replace("-", "")); S[ts]["c"].append(float(c))
        except Exception:
            pass
    c2.close()
    import json as _json, glob as _glob
    def _op_of(d8):
        for p in ["data/_bt_year/%s/wave_state.json" % d8] + _glob.glob("data/_bt_*/%s/wave_state.json" % d8):
            try:
                return str((_json.load(open(p, encoding="utf-8")) or {}).get("operation") or "?").lower()
            except Exception:
                continue
        return "?"
    recs = []
    for sym, dstr, vr, et, st in rows:
        m = re.match(r"^(SH|SZ|BJ)(\d{6})$", str(sym or "").upper())
        ts = ("%s.%s" % (m.group(2), m.group(1))) if m else None
        if not ts or ts not in S:
            continue
        s = S[ts]; d8 = str(dstr).replace("-", "")[:8]
        i = None
        for k in range(len(s["d"]) - 1, -1, -1):
            if s["d"][k] <= d8:
                i = k; break
        if i is None or i + 10 >= len(s["c"]):
            continue
        px = s["c"][i]
        f5 = (s["c"][i + 5] / px - 1) * 100
        f10 = (s["c"][i + 10] / px - 1) * 100
        lo10 = min(s["c"][i:min(i + 11, len(s["c"]))])
        recs.append({"vr": float(vr), "st": st, "op": _op_of(d8), "f5": f5, "f10": f10,
                     "mae": (lo10 / px - 1) * 100})
    print("  可用样本 = %d 条 ✓\n" % len(recs))
    print("  %-18s %6s %10s %8s %10s %9s" % ("量比档", "n", "+5d中位", "胜率%", "+10d中位", "MAE中位%"))
    bins = [(0, 0.7), (0.7, 0.85), (0.85, 1.0), (1.0, 1.2), (1.2, 1.5), (1.5, 99)]
    for lo, hi in bins:
        sub = [r for r in recs if lo <= r["vr"] < hi]
        if len(sub) < 5:
            continue
        m5 = statistics.median([r["f5"] for r in sub]); w = 100.0*sum(1 for r in sub if r["f5"] > 0)/len(sub)
        m10 = statistics.median([r["f10"] for r in sub]); mae = statistics.median([r["mae"] for r in sub])
        print("  %-18s %6d %10.2f %8.1f %10.2f %9.2f" % ("%.2f~%.2f" % (lo, hi), len(sub), m5, w, m10, mae))
    print("\n  【按浪型档位 × 量比档 交叉 ✓（关键 ✓：防止「档位混淆」✗）】")
    print("  %-10s %-16s %6s %10s %8s %10s" % ("档位", "量比档", "n", "+5d中位", "胜率%", "+10d中位"))
    for op in sorted({r["op"] for r in recs}):
        for lo, hi in bins:
            sub = [r for r in recs if r["op"] == op and lo <= r["vr"] < hi]
            if len(sub) < 8:
                continue
            m5 = statistics.median([r["f5"] for r in sub]); w = 100.0*sum(1 for r in sub if r["f5"] > 0)/len(sub)
            m10 = statistics.median([r["f10"] for r in sub])
            print("  %-10s %-16s %6d %10.2f %8.1f %10.2f" % (op, "%.2f~%.2f" % (lo, hi), len(sub), m5, w, m10))
    print("\n  累积视角 ✓（「量比 ≥ X 才买」在各 X 下的表现 ✓）")
    print("  %-10s %6s %10s %8s" % ("X", "n", "+5d中位", "胜率%"))
    for x in (0.7, 0.8, 0.85, 0.9, 0.95, 1.0, 1.1):
        sub = [r for r in recs if r["vr"] >= x]
        if len(sub) < 5:
            continue
        m5 = statistics.median([r["f5"] for r in sub]); w = 100.0*sum(1 for r in sub if r["f5"] > 0)/len(sub)
        print("  ≥%-8.2f %6d %10.2f %8.1f" % (x, len(sub), m5, w))

if __name__ == "__main__":
    main()
