# -*- coding: utf-8 -*-
"""G7 的公平检验：**大跌日 × 买入位置（支撑/低位）**（§9.813，用户提示 ✓）

用户 2026-10-13：「G7 是有其他条件的吧，比如大跌 + 底部支撑买入这样」✓
代码事实（`wolf_day_rules.py` 头注 ✓）：G7 的**买入侧并未实现** ✗——
  原文：「买侧我们本来就"只在下跌里买"（狼大原话），故不额外放宽——**不新造"大跌日放宽买入"的机制**」✓
  ⇒ 所以上一节（§9.812）拿"弱日买入表现"检验 G7 **对不上靶** ✗。
本脚本改测**你说的那种组合** ✓：在指数大跌日/弱势日里，**按买入位置分层**（当日区间位置 ✓，代理"支撑/低位" ✓）
  —— 即"低位买入在弱日是否真比中高位好" ✓，这才是 G7「大跌+底部」的可检验形式 ✓。

口径：交易日 = 买成交日 ✓；位置 = (买价 − 当日最低) / (当日最高 − 当日最低) ✓（`mkt_bars_daily` ✓）；
     日型 = `wolf_day_rules.day_bias`（000001.SH ✓）；结局 = 买→同臂同标的下一次卖（近似 FIFO ✓）。
"""
import json
import os
import statistics
import sys
from collections import defaultdict

import psycopg2

DSN = "postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.services import wolf_day_rules as W  # noqa: E402


def _norm(sym):
    """统一标的代码：SH600127 / 600127.SH / 600127 → 600127.SH ✓（首轮因格式不匹配导致 1614 组全丢 ✗）"""
    s = str(sym or "").strip().upper()
    if "." in s:
        a, b = s.split(".", 1)
        return ("%s.%s" % (a, b)) if a.isdigit() else ("%s.%s" % (b, a))
    if len(s) == 8 and s[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (s[2:], s[:2])
    if s.isdigit() and len(s) == 6:
        return "%s.%s" % (s, "SH" if s[0] in ("6", "5", "9") else "SZ")
    return s


def main():
    hist = json.load(open("data/index_daily_hist.json", encoding="utf-8"))["000001.SH"]
    dt = {str(r["trade_date"])[:8]: W.day_bias(float(r["pct_chg"])) for r in hist if r.get("pct_chg") is not None}
    c = psycopg2.connect(DSN, connect_timeout=8)
    cur = c.cursor()
    cur.execute("""select account_id, symbol, trade_date, direction, price, volume, coalesce(reason,'')
                   from paper_trades where coalesce(voided,0)=0 order by account_id, symbol, created_at""")
    rows = cur.fetchall()
    pos = defaultdict(list); pairs = []
    for acc, sym, d, dr, px, vol, rs in rows:
        k = (acc, sym); drl = str(dr or "").lower(); dd = str(d).replace("-", "")[:8]
        if drl in ("buy", "买入"):
            pos[k].append([float(px or 0), float(vol or 0), dd, rs])
        elif drl in ("sell", "卖出") and pos[k]:
            bpx, bvol, bday, brs = pos[k].pop(0)
            q = min(bvol, float(vol or 0))
            pairs.append({"sym": sym, "day": bday, "buy": bpx, "qty": q,
                          "pnl": (float(px or 0) - bpx) * q, "reason": brs})
    # 当日区间位置
    day_range = {}
    cur.execute("""select ts_code, trade_date, low, high from mkt_bars_daily
                   where trade_date >= '20260101' and low is not null and high is not null""")
    for code, d, lo, hi in cur.fetchall():
        day_range[(_norm(code), str(d).replace("-", "")[:8])] = (float(lo), float(hi))
    grp = defaultdict(lambda: {"n": 0, "pnl": 0.0, "lst": []})
    nomatch = 0
    for p in pairs:
        rng = day_range.get((_norm(p["sym"]), p["day"]))
        if not rng or rng[1] <= rng[0]:
            nomatch += 1
            continue
        lo, hi = rng
        q = (p["buy"] - lo) / (hi - lo)
        band = "低位(≤30%)" if q <= 0.30 else ("中位" if q < 0.70 else "高位(≥70%)")
        bias = dt.get(p["day"], "neutral")
        weak = "指数弱(down)" if bias == "down" else ("指数强(up)" if bias == "up" else "指数中性")
        g = grp[(weak, band)]
        g["n"] += 1; g["pnl"] += p["pnl"]; g["lst"].append(p["pnl"])
    print("配对 %d 组（无当日区间 %d 组 ✗）｜分组 = 指数日型 × 买入位置" % (len(pairs), nomatch))
    print("\n%-16s %-12s %6s %14s %12s %12s" % ("指数日型", "买入位置", "笔数", "合计盈亏", "单笔均值", "单笔中位"))
    for k in sorted(grp, key=lambda x: (x[0], x[1])):
        g = grp[k]
        if g["n"] < 3:
            continue
        print("%-16s %-12s %6d %14.0f %12.1f %12.1f"
              % (k[0], k[1], g["n"], g["pnl"], g["pnl"] / g["n"], statistics.median(g["lst"])))
    print("\n（位置 = (买价−当日最低)/(当日最高−当日最低) ✓ 代理「底部支撑」；n<3 的组不打印 ✓）")


if __name__ == "__main__":
    main()
