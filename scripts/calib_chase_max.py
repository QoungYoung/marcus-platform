# -*- coding: utf-8 -*-
"""calib_chase_max.py — 标定「相对突破参照位的追高上限」（§9.858，用户选 A ✓）

【要回答 ✓】`WOLF_BUILD_CHASE_MAX=3%`（**我设的代理值 ✗，无语料数字 ✓**）到底该设多少？
  语料只给了"**不在开盘追**"（ledger §29.1 ✓）与"冲上去一定不能追"（无数字 ✗）。
【做法 ✓】用**真实成交**（paper_trades 里 trend_break 的买入 ✓，即系统真买了的 ✓）：
  · 特征 = **成交价 ÷ 该腿的突破参照位(level) − 1** ✓（= "比参照位追高了几个点" ✓）
  · 结果 = 同一复权序列的 T+5 / T+10 / MAE ✓（从成交当天收盘起算 ✓ 与特征解耦 ✓）
  · 分档 ⇒ 看"偏离越大是否越差"✓ ⇒ 用数据定阈值 ✓（而不是拍脑袋 ✗）
用法：python3 scripts/calib_chase_max.py
"""
from __future__ import annotations
import collections, glob, json, os, re, sqlite3, statistics

def main():
    import psycopg2
    cn = psycopg2.connect("postgresql://marcus:marcus123@127.0.0.1:5433/marcus_trading"); cur = cn.cursor()
    cur.execute("""select account_id, symbol, trade_date::text, price from paper_trades
                   where direction like '买%' and coalesce(voided,0)=0 and coalesce(reason,'') like '%trend_break%'
                   order by trade_date""")
    trades = cur.fetchall(); cn.close()
    print("  trend_break 真实买入（paper_trades ✓）= %d 笔" % len(trades))
    # 腿的参照位（按 日+标的 从各臂天目录取 ✓）
    lvl = {}
    for f in glob.glob("data/_bt_*/2026*/legs.jsonl"):
        day = os.path.basename(os.path.dirname(f))
        try:
            for l in open(f, encoding="utf-8"):
                if not l.strip():
                    continue
                r = json.loads(l)
                if str(r.get("type")) == "trend_break":
                    lvl[(day, str(r.get("symbol")))] = r.get("level")
        except Exception:
            continue
    # 复权序列
    S = collections.defaultdict(lambda: {"d": [], "c": []})
    c2 = sqlite3.connect('data/_bt_full/bars_adj.sqlite'); cu = c2.cursor()
    for ts, d, c in cu.execute("select ts_code, trade_date, close from bars order by ts_code, trade_date"):
        try: S[ts]["d"].append(str(d)); S[ts]["c"].append(float(c))
        except Exception: pass
    c2.close()
    recs = []
    for acct, sym, td, px in trades:
        d8 = str(td).replace("-", "")[:8]
        m = re.match(r"^(SH|SZ|BJ)(\d{6})$", str(sym or "").upper())
        ts = ("%s.%s" % (m.group(2), m.group(1))) if m else None
        if not ts or ts not in S:
            continue
        lv = lvl.get((d8, sym)) or lvl.get((d8, str(sym).upper()))
        try:
            lv = float(lv or 0)
            px = float(px or 0)
        except Exception:
            continue
        if lv <= 0 or px <= 0:
            continue
        s = S[ts]; i = None
        for k in range(len(s["d"]) - 1, -1, -1):
            if s["d"][k] <= d8:
                i = k; break
        if i is None or i + 10 >= len(s["c"]):
            continue
        c0 = s["c"][i]
        dev = (px / lv - 1) * 100
        lo10 = min(s["c"][i:min(i + 11, len(s["c"]))])
        recs.append({"acct": acct, "sym": sym, "day": d8, "dev": dev, "lvl": lv, "px": px,
                     "f5": (s["c"][i+5]/c0 - 1)*100, "f10": (s["c"][i+10]/c0 - 1)*100,
                     "mae": (lo10/c0 - 1)*100})
    print("  可用样本 = %d 笔 ✓\n" % len(recs))
    if not recs:
        print("  （样本为 0 ✗ ⇒ 需要更多成交 ⇒ 等臂继续跑 ✓）"); return
    print("  逐笔 ✓：")
    for r in sorted(recs, key=lambda x: -x["dev"])[:15]:
        print("   %s %s %s | 参照=%.2f 成交=%.2f ⇒ **偏离 %+5.2f%%** | +5d=%+7.2f%% +10d=%+7.2f%% MAE=%+6.2f%%"
              % (r["acct"][:10], r["sym"], r["day"], r["lvl"], r["px"], r["dev"], r["f5"], r["f10"], r["mae"]))
    print("\n  分档 ✓（用数据看「偏离多少以上开始变差」✓）")
    print("  %-14s %6s %10s %8s %10s %9s" % ("偏离档", "n", "+5d中位", "胜率%", "+10d中位", "MAE中位%"))
    for lo, hi in ((-99, 0), (0, 1), (1, 2), (2, 3), (3, 99)):
        sub = [r for r in recs if lo <= r["dev"] < hi]
        if not sub:
            continue
        print("  %-14s %6d %10.2f %8.1f %10.2f %9.2f" % (
            "%.0f~%.0f%%" % (lo, hi), len(sub),
            statistics.median([r["f5"] for r in sub]),
            100.0*sum(1 for r in sub if r["f5"] > 0)/len(sub),
            statistics.median([r["f10"] for r in sub]),
            statistics.median([r["mae"] for r in sub])))
    print("\n  累积视角 ✓（「偏离 ≤ X 的样本表现」⇒ 用来选阈值 ✓）")
    print("  %-10s %6s %10s %8s" % ("X", "n", "+5d中位", "胜率%"))
    for x in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 99):
        sub = [r for r in recs if r["dev"] <= x]
        if len(sub) < 3:
            continue
        print("  ≤%-8.1f %6d %10.2f %8.1f" % (x, len(sub),
              statistics.median([r["f5"] for r in sub]),
              100.0*sum(1 for r in sub if r["f5"] > 0)/len(sub)))

if __name__ == "__main__":
    main()
