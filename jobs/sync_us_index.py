# -*- coding: utf-8 -*-
"""sync_us_index.py — 美股指数/个股日线缓存（datahub tushare 中继 ✓ index_global / us_daily）

为什么：用户 2026-10-16 提出「纳指新高 ⇒ 美国资本等一个风出货 ⇒ CPI 就是那个风」的读法。
  要检验它（新高 ∧ 事件窗口的**尾部**概率）必须先有纳指历史 ✓。
  relay 实测：index_global IXIC/SPX 各 4000 根到 **2026-10-15** ✓（NDX 滞后 5 个月 ✗ 别用）；
  us_daily 个股 5000 根到 2026-10-15 ✓。

输出：data/index_global_<code>.json = {"code","src","rows":[[date, close, high, amount], ...]} ✓
  （与浪型链/指数缓存同 4 字段序，便于复用 ✓；amount 用 change 的绝对值占位 ✗ 无成交额接口 ⇒ 标注 src ✓）
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
from tushare_relay import relay_query

DATA = os.environ.get("DATA_DIR", "data")
GLOBAL = ["IXIC", "SPX"]          # NDX 滞后，不取 ✓
STOCKS = ["AAPL", "NVDA", "TSLA"]  # 几只观察用；按需增删 ✓


def write(code, rows, src):
    p = os.path.join(DATA, "index_global_%s.json" % code)
    json.dump({"code": code, "src": src, "rows": rows}, open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
    os.replace(p + ".tmp", p)
    print("  %-6s n=%-5d %s → %s ✓" % (code, len(rows), rows[0][0], rows[-1][0]))


def main():
    os.makedirs(DATA, exist_ok=True)
    for code in GLOBAL:
        try:
            df = relay_query("index_global", ts_code=code)
            rows = sorted([[str(r["trade_date"]), float(r["close"]), float(r.get("high") or r["close"]),
                            abs(float(r.get("change") or 0))] for _, r in df.iterrows()])
            write(code, rows, "tushare_relay:index_global")
        except Exception as e:
            print("  %-6s 失败 %s %s" % (code, type(e).__name__, str(e)[:80]))
    for code in STOCKS:
        try:
            df = relay_query("us_daily", ts_code=code)
            rows = sorted([[str(r["trade_date"]), float(r["close"]), float(r.get("high") or r["close"]),
                            float(r.get("vol") or 0)] for _, r in df.iterrows()])
            write(code, rows, "tushare_relay:us_daily")
        except Exception as e:
            print("  %-6s 失败 %s %s" % (code, type(e).__name__, str(e)[:80]))


if __name__ == "__main__":
    main()
