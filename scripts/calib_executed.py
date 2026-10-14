# -*- coding: utf-8 -*-
"""calib_executed.py — **只看"系统真会买"的样本**（§9.854，用户「重做」✓）

【为什么要重做 ✗】上一版标定的样本里 **89.6% 是 blocked** ✗（被硬闸拦下的）⇒ 等于统计了
  "被拦下的那批的后续走势" ✗ ⇒ 出现 +36%/胜率 93.8% 的荒谬值 ✓（典型案例 SZ301611 是
  [COND-TIMING] 不在下跌中买 ⇒ 系统**正确地没买** ✓）。本脚本把口径改成"**真会买**" ✓：
    · 主口径 = `status='executed'` ✓（= 真的成交了 ✓）
    · 次口径 = 非 blocked（executed + await_retry ✓ = 过了硬闸、由 AI 决定 ✓）
用法：python3 scripts/calib_executed.py [--kind trend_break] [--db ...]
"""
from __future__ import annotations
import argparse, collections, glob, json, os, re, sqlite3, statistics

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="trend_break")
    ap.add_argument("--db", default="data/_bt_full/bars_adj.sqlite")
    ap.add_argument("--dsn", default="postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    a = ap.parse_args()
    import psycopg2
    cn = psycopg2.connect(a.dsn); cur = cn.cursor()
    cur.execute("""
        select account_id, symbol, created_at::date::text, coalesce(status,''),
               coalesce((snapshot->'fields'->>'vol_ratio')::float, 0), coalesce(left(reason,110),'')
        from t_triggers where event_type like %s order by created_at""", ("%" + a.kind + "%",))
    rows = cur.fetchall(); cn.close()
    st = collections.Counter(r[3] for r in rows)
    print("  %s 触发总数 = %d ✓ 状态: %s" % (a.kind, len(rows), dict(st)))
    S = collections.defaultdict(lambda: {"d": [], "c": []})
    c2 = sqlite3.connect(a.db); cu = c2.cursor()
    for ts, d, c in cu.execute("select ts_code, trade_date, close from bars order by ts_code, trade_date"):
        try: S[ts]["d"].append(str(d)); S[ts]["c"].append(float(c))
        except Exception: pass
    c2.close()
    def op_of(d8):
        for p in ["data/_bt_year/%s/wave_state.json" % d8] + glob.glob("data/_bt_*/%s/wave_state.json" % d8):
            try: return str((json.load(open(p, encoding="utf-8")) or {}).get("operation") or "?").lower()
            except Exception: continue
        return "?"
    def collect(statuses):
        out = []
        for acct, sym, dstr, s2, vr, rs in rows:
            if s2 not in statuses:
                continue
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
            i20 = max(0, i - 20)
            g20 = (px / s["c"][i20] - 1) * 100 if s["c"][i20] else 0
            hi250 = max(s["c"][max(0, i - 249):i + 1])
            out.append({"sym": sym, "day": d8, "acct": acct, "vr": float(vr), "op": op_of(d8),
                        "near": (px / hi250 - 1) * 100 if hi250 else 0, "g20": g20,
                        "f5": (s["c"][i + 5] / px - 1) * 100, "f10": (s["c"][i + 10] / px - 1) * 100,
                        "rs": rs})
        return out
    for label, sts in (("主口径：executed（真成交 ✓）", {"executed"}),
                       ("次口径：非 blocked（过了硬闸 ✓）", {"executed", "await_retry", "pending"})):
        recs = collect(sts)
        print("\n  【%s】样本 = %d 条" % (label, len(recs)))
        if not recs:
            print("    （无样本 ⇒ **本阶段无法标定** ✗ ⇒ 需要先跑出成交（t53 ✓）✓）")
            continue
        m5 = statistics.median([r["f5"] for r in recs]); m10 = statistics.median([r["f10"] for r in recs])
        w = 100.0 * sum(1 for r in recs if r["f5"] > 0) / len(recs)
        print("    +5d 中位=%+.2f%%  胜率=%.1f%%  +10d 中位=%+.2f%%" % (m5, w, m10))
        print("    明细（前 8 条 ✓）:")
        for r in recs[:8]:
            print("     %s %s @%s | 量比=%.2f 档=%s 距高=%+.1f%% 20日涨=%+.1f%% ⇒ +5d=%+.2f%% | %s"
                  % (r["acct"][:10], r["sym"], r["day"], r["vr"], r["op"], r["near"], r["g20"], r["f5"], r["rs"][:50]))
        # 特征分档（小样本只给中位 ✓）
        for key, bins in (("vr", [(0, 0.85), (0.85, 1.0), (1.0, 9)]), ("near", [(-99, -10), (-10, -2), (-2, 99)]),
                          ("g20", [(-99, 10), (10, 30), (30, 999)])):
            print("    —— 按 %s 分档 ——" % key)
            for lo, hi in bins:
                sub = [r for r in recs if lo <= r[key] < hi]
                if not sub:
                    continue
                print("      %-10s n=%-3d +5d中位=%+7.2f%%  胜率=%5.1f%%" % (
                    "%.2f~%.2f" % (lo, hi) if key == "vr" else "%.0f~%.0f" % (lo, hi), len(sub),
                    statistics.median([r["f5"] for r in sub]),
                    100.0 * sum(1 for r in sub if r["f5"] > 0) / len(sub)))

if __name__ == "__main__":
    main()
