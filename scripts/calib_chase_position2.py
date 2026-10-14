# -*- coding: utf-8 -*-
"""calib_chase_position2.py — 追高/位置闸阈值标定 v2（§9.843）

v1 的问题 ✗（已核实）：入场基准用了"成交日**收盘**"（而实际是盘中成交价 ✓）⇒ 系统性抬高收益 ✗
v2 修正 ✓：
  · 基准 = **实际成交价**（`paper_trades.price` ✓）
  · 序列 = **不复权** `bars.sqlite` ✓（与成交价同口径 ✓）
  · 指标 = +5/+10/+20 交易日收益 ✓ **＋ MAE（入场后 10 日内最大不利偏移 ✓）**
  · 样本可切：`--accounts`（逗号分隔，支持 like ✓）⇒ 同口径子集 ✓
  · 分档：距 250 日高 × 20 日涨幅 ✓；并按腿型给 n≥阈值 的格子 ✓
用法：python3 scripts/calib_chase_position2.py [--accounts all|<like 列表>] [--min-n 20]
"""
from __future__ import annotations
import argparse, collections, os, re, sqlite3, statistics

def load_bars(db):
    cn = sqlite3.connect(db); cur = cn.cursor()
    cur.execute("select ts_code, trade_date, close, high, low from bars order by ts_code, trade_date")
    S = collections.defaultdict(lambda: {"d": [], "c": [], "h": [], "l": []})
    for ts, d, c, h, l in cur:
        try:
            c = float(c); h = float(h or c); l = float(l or c)
        except Exception:
            continue
        s = S[ts]; s["d"].append(str(d).replace("-", "")); s["c"].append(c); s["h"].append(h); s["l"].append(l)
    cn.close(); return S

def idx_at(s, d8):
    for k in range(len(s["d"]) - 1, -1, -1):
        if s["d"][k] <= d8:
            return k
    return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/_bt_full/bars.sqlite")
    ap.add_argument("--dsn", default="postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading")
    ap.add_argument("--accounts", default="all")
    ap.add_argument("--min-n", type=int, default=20)
    a = ap.parse_args()
    import psycopg2
    cn = psycopg2.connect(a.dsn); cur = cn.cursor()
    if a.accounts.strip().lower() == "all":
        cur.execute("""select symbol, trade_date::text, price, coalesce(reason,''), account_id from paper_trades
                       where direction like '买%' and coalesce(voided,0)=0 order by trade_date""")
    else:
        pats = [x.strip() for x in a.accounts.split(',') if x.strip()]
        cur.execute("""select symbol, trade_date::text, price, coalesce(reason,''), account_id from paper_trades
                       where direction like '买%' and coalesce(voided,0)=0
                         and account_id like any(%s) order by trade_date""", (['%' + p + '%' for p in pats],))
    rows = cur.fetchall(); cn.close()
    print("  成交样本 = %d 笔（accounts=%s ✓）" % (len(rows), a.accounts))
    S = load_bars(a.db)
    recs = []
    for sym, tdate, price, reason, acct in rows:
        s0 = str(sym or "").upper()
        m = re.match(r"^(SH|SZ|BJ)(\d{6})$", s0)
        ts = ("%s.%s" % (m.group(2), m.group(1))) if m else (s0 if re.match(r"^\d{6}\.(SH|SZ|BJ)$", s0) else None)
        if not ts or ts not in S:
            continue
        d8 = str(tdate).replace("-", "")[:8]
        i = idx_at(S[ts], d8)
        if i is None or i < 21:
            continue
        try:
            px = float(price or 0)
        except Exception:
            px = 0
        if px <= 0:
            continue
        s = S[ts]
        hi250 = max(s["h"][max(0, i - 249):i + 1]); hi20 = max(s["h"][max(0, i - 19):i + 1])
        g20 = (px / s["c"][i - 20] - 1) * 100 if s["c"][i - 20] else 0
        def fwd(n):
            j = i + n
            return (s["c"][j] / px - 1) * 100 if j < len(s["c"]) else None
        lo10 = min(s["l"][i:min(i + 11, len(s["l"]))]) if i < len(s["l"]) else px
        kind = ""
        mm = re.search(r"（([a-z0-9_]+)）", reason or "")
        if mm: kind = mm.group(1)
        elif "埋伏" in (reason or "") or "转正" in (reason or ""): kind = "ambush_add"
        recs.append({"sym": ts, "d": d8, "kind": kind or "(未标)", "d250": (px / hi250 - 1) * 100 if hi250 else 0,
                     "d20": (px / hi20 - 1) * 100 if hi20 else 0, "g20": g20, "f5": fwd(5), "f10": fwd(10),
                     "f20": fwd(20), "mae": (lo10 / px - 1) * 100 if px else None})
    print("  可用样本 = %d 笔 ✓\n" % len(recs))
    def show(title, keybins, key):
        print("  【%s】" % title)
        print("    %-18s %6s %10s %8s %10s %9s" % ("档位", "n", "+10d中位", "胜率%", "+20d中位", "MAE中位%"))
        for lo, hi in keybins:
            sub = [r for r in recs if lo <= r[key] < hi and r["f10"] is not None and r["mae"] is not None]
            if len(sub) < a.min_n:
                continue
            med = statistics.median([r["f10"] for r in sub])
            win = 100.0 * sum(1 for r in sub if r["f10"] > 0) / len(sub)
            s20 = [r["f20"] for r in sub if r["f20"] is not None]
            med20 = statistics.median(s20) if s20 else float("nan")
            mae = statistics.median([r["mae"] for r in sub])
            print("    %-18s %6d %10.2f %8.1f %10.2f %9.2f" % ("%.0f~%.0f%%" % (lo, hi), len(sub), med, win, med20, mae))
        print()
    show("距 250 日高（成交价 vs 当日可见的 250 日最高 ✓）",
         [(-999, -10), (-10, -5), (-5, -2), (-2, 0), (0, 999)], "d250")
    show("20 日涨幅（成交价 vs 20 日前收盘 ✓）",
         [(-999, 0), (0, 10), (10, 20), (20, 30), (30, 999)], "g20")
    print("  【腿型分组（n≥%d ✓）】" % a.min_n)
    print("    %-20s %6s %10s %8s %9s" % ("腿型", "n", "+10d中位", "胜率%", "MAE中位%"))
    for kind in sorted({r["kind"] for r in recs}):
        sub = [r for r in recs if r["kind"] == kind and r["f10"] is not None and r["mae"] is not None]
        if len(sub) < a.min_n:
            continue
        med = statistics.median([r["f10"] for r in sub]); win = 100.0 * sum(1 for r in sub if r["f10"] > 0) / len(sub)
        mae = statistics.median([r["mae"] for r in sub])
        print("    %-20s %6d %10.2f %8.1f %9.2f" % (kind, len(sub), med, win, mae))

if __name__ == "__main__":
    main()
