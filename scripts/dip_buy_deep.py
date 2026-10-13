# -*- coding: utf-8 -*-
"""低位买点再拆一层：腿型 × 位置 × 放量（§9.814，用户「做吧」✓）

目的（承接 §9.813）：低位买点整体是赚的 ✓，而 B 闸（`不在下跌中买` ✗）会优先拦掉"下跌中"的买点 ✗
 ⇒ 本脚本把"低位买点"按**腿型**拆开（`paper_trades.reason` ✓：custom_prevlow=挂昨日低点 / custom_m5dump=分时急杀 /
   trend_break_buy / wolf_ambush_buy / low_buy ✓），并算**是否真挂在昨日低点** ✓、**放量倍数** ✓
 ⇒ 看 B 闸拦下的到底是"低位好点"还是"急杀陷阱" ✓

数据：bt PG paper_trades（成交通道 ✓）+ mkt_bars_daily（当日/前日区间 + 成交量 ✓）
拦截画像：各臂 prod_*.json 的 `[COND-TIMING]` 触发（含 event_type / quote_price ✓）
"""
import glob
import json
import os
import statistics
import sys
from collections import Counter, defaultdict

import psycopg2

DSN = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"


def _norm(sym):
    s = str(sym or "").strip().upper()
    if "." in s:
        a, b = s.split(".", 1)
        return "%s.%s" % (a, b) if a.isdigit() else "%s.%s" % (b, a)
    if len(s) == 8 and s[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (s[2:], s[:2])
    if s.isdigit() and len(s) == 6:
        return "%s.%s" % (s, "SH" if s[0] in ("6", "5", "9") else "SZ")
    return s


def _leg(reason):
    r = str(reason or "")
    for k, name in (("custom_prevlow", "挂昨低(prevlow)"), ("custom_m5dump", "分时急杀(m5dump)"),
                    ("trend_break_buy", "trend_break_buy"), ("wolf_ambush_buy", "埋伏(ambush)"),
                    ("low_buy", "low_buy"), ("dca", "DCA/其他"), ("DCA", "DCA/其他")):
        if k in r:
            return name
    return "其他"


def main():
    c = psycopg2.connect(DSN, connect_timeout=8)
    cur = c.cursor()
    # 日线区间 + 成交量（含前一日 ⇒ 判断"挂昨低" ✓）
    cur.execute("""select ts_code, trade_date, low, high, close, vol from mkt_bars_daily
                   where trade_date >= '20251201' and low is not null""")
    bars = defaultdict(dict)
    for code, d, lo, hi, cl, vol in cur.fetchall():
        bars[_norm(code)][str(d).replace("-", "")[:8]] = (float(lo), float(hi), float(cl or 0), float(vol or 0))
    for k in bars:
        for d in bars[k]:
            ds = sorted(bars[k])
            i = ds.index(d)
            bars[k][d] = bars[k][d] + ((bars[k][ds[i - 1]][0] if i > 0 else None),)   # 前一日最低
    # 买→卖配对
    cur.execute("""select account_id, symbol, trade_date, direction, price, volume, coalesce(reason,'')
                   from paper_trades where coalesce(voided,0)=0 order by account_id, symbol, created_at""")
    pos = defaultdict(list); pairs = []
    for acc, sym, d, dr, px, vol, rs in cur.fetchall():
        k = (acc, sym); drl = str(dr or "").lower(); dd = str(d).replace("-", "")[:8]
        if drl in ("buy", "买入"):
            pos[k].append([float(px or 0), float(vol or 0), dd, rs])
        elif drl in ("sell", "卖出") and pos[k]:
            bpx, bvol, bday, brs = pos[k].pop(0)
            q = min(bvol, float(vol or 0))
            pairs.append({"sym": _norm(sym), "day": bday, "buy": bpx, "qty": q,
                          "pnl": (float(px or 0) - bpx) * q, "reason": brs})
    grp = defaultdict(lambda: {"n": 0, "pnl": 0.0, "lst": [], "prevlow": 0, "volx": []})
    for p in pairs:
        b = bars.get(p["sym"], {}).get(p["day"])
        if not b:
            continue
        lo, hi, cl, vol, plo = b
        q = (p["buy"] - lo) / (hi - lo) if hi > lo else 0.5
        band = "低位" if q <= 0.30 else ("中位" if q < 0.70 else "高位")
        leg = _leg(p["reason"])
        g = grp[(band, leg)]
        g["n"] += 1; g["pnl"] += p["pnl"]; g["lst"].append(p["pnl"])
        if plo and abs(p["buy"] - plo) / plo <= 0.003:
            g["prevlow"] += 1
        ds = sorted(bars[p["sym"]]); i = ds.index(p["day"])
        v5 = [bars[p["sym"]][ds[j]][3] for j in range(max(0, i - 5), i)]
        if v5 and statistics.mean(v5) > 0:
            g["volx"].append(vol / statistics.mean(v5))
    print("配对 %d 组 ⇒ 分组：位置 × 腿型（`挂昨低` = 成交价≈前一日最低 ±0.3%% ✓）" % len(pairs))
    print("\n%-6s %-20s %6s %14s %12s %10s %8s" % ("位置", "腿型", "笔数", "合计", "单笔均值", "挂昨低", "量比x"))
    for k in sorted(grp, key=lambda x: (x[0], -grp[x]["n"])):
        g = grp[k]
        if g["n"] < 3:
            continue
        print("%-6s %-20s %6d %14.0f %12.1f %10s %8s"
              % (k[0], k[1], g["n"], g["pnl"], g["pnl"] / g["n"],
                 "%d(%.0f%%)" % (g["prevlow"], 100.0 * g["prevlow"] / g["n"]),
                 ("%.2f" % statistics.mean(g["volx"])) if g["volx"] else "-"))
    # B 闸拦截画像
    blk = Counter(); blk_pos = defaultdict(list)
    for root in ("data/_bt_t41", "data/_bt_c2610122052", "data/_bt_draby26", "data/_bt_draymar"):
        for fp in glob.glob(os.path.join(root, "_summary", "prod_*.json")):
            try:
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            day = str(d.get("day") or "")
            for t in (d.get("triggers") or []):
                if "[COND-TIMING]" not in str(t.get("reason") or ""):
                    continue
                et = str(t.get("event_type") or "?")
                try:
                    qp = float(t.get("quote_price") or 0)
                except Exception:
                    qp = 0.0
                blk[et] += 1
                b = bars.get(_norm(t.get("symbol")), {}).get(day)
                if b and b[1] > b[0] and qp:
                    blk_pos[et].append((qp - b[0]) / (b[1] - b[0]))
    print("\n=== 被 B/C 闸（[COND-TIMING]）拦下的触发画像 ===")
    for et, n in blk.most_common(8):
        ps = blk_pos.get(et) or []
        m = ("位置均值 %.2f（低位占比 %.0f%%）" % (statistics.mean(ps),
             100.0 * sum(1 for x in ps if x <= 0.30) / len(ps))) if ps else "（无当日区间 ✗）"
        print("  %-22s %4d 笔  %s" % (et, n, m))


if __name__ == "__main__":
    main()


# ── 反事实（粗口径）：被 B/C 闸拦下的买点"若按 quote_price 成交"当天/次日收盘如何 ──
def counterfactual():
    import psycopg2 as _pg
    c = _pg.connect(DSN, connect_timeout=8)
    cur = c.cursor()
    cur.execute("""select ts_code, trade_date, close from mkt_bars_daily
                   where trade_date >= '20251201' and close is not null""")
    cl = {}
    for code, d, close in cur.fetchall():
        cl.setdefault(_norm(code), {})[str(d).replace("-", "")[:8]] = float(close)
    rows = []
    for root in ("data/_bt_t41", "data/_bt_c2610122052", "data/_bt_draby26", "data/_bt_draymar"):
        for fp in glob.glob(os.path.join(root, "_summary", "prod_*.json")):
            try:
                d = json.load(open(fp, encoding="utf-8"))
            except Exception:
                continue
            day = str(d.get("day") or "")
            for t in (d.get("triggers") or []):
                if "[COND-TIMING]" not in str(t.get("reason") or ""):
                    continue
                try:
                    qp = float(t.get("quote_price") or 0)
                except Exception:
                    qp = 0.0
                sym = _norm(t.get("symbol"))
                if not qp or not sym:
                    continue
                ds = sorted(cl.get(sym, {}))
                if day not in ds:
                    continue
                i = ds.index(day)
                same = cl[sym][day]
                nxt = cl[sym][ds[i + 1]] if i + 1 < len(ds) else None
                rows.append((str(t.get("event_type") or "?"), day, sym,
                             100.0 * (same - qp) / qp, (100.0 * (nxt - qp) / qp) if nxt else None))
    agg = {}
    for et, day, sym, r0, r1 in rows:
        a = agg.setdefault(et, {"n": 0, "s0": 0.0, "s1": 0.0, "n1": 0})
        a["n"] += 1; a["s0"] += r0
        if r1 is not None:
            a["s1"] += r1; a["n1"] += 1
    print("\n=== 反事实（粗口径 ✓）：被 B/C 闸拦下的买点，若以 quote_price 买入 —— 当日/次日收盘涨幅 ===")
    print("%-24s %5s %14s %14s" % ("event_type", "笔数", "当日收盘均值%", "次日收盘均值%"))
    for et, a in sorted(agg.items(), key=lambda x: -x[1]["n"]):
        if a["n"] < 3:
            continue
        print("%-24s %5d %14.2f %14s" % (et, a["n"], a["s0"] / a["n"],
                                         ("%.2f" % (a["s1"] / a["n1"])) if a["n1"] else "-"))
    print("（粗口径说明 ✗：用**当日收盘**当卖点、不含 T 腿的止盈/止损规则 ✓ ⇒ 只看方向 ✓）")


if __name__ == "__main__":
    counterfactual()
