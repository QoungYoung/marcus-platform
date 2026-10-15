# -*- coding: utf-8 -*-
"""sync_index_weekly.py — 指数**周线**缓存（datahub index_weekly ✓），供等级模型实验/周线档使用。

输出与浪型链同格式：data/index_weekly_<code>.json = {"code","src","rows":[[date, close, high, amount], ...]} ✓
（注意：**不让浪型链的生产口径读它** —— 链的状态继续用日线 CSV（600 根）✓，
  周线只用于"等级"这条独立线 ✓ 免得挪动 1 浪顶 3674.40 ✗。）
"""
import json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core"))
from tushare_relay import relay_query

DATA = os.environ.get("DATA_DIR", "data")
CODES = ["000001.SH", "399001.SZ", "399006.SZ"]


def main():
    os.makedirs(DATA, exist_ok=True)
    for code in CODES:
        try:
            df = relay_query("index_weekly", ts_code=code, start_date="20000101", end_date=time.strftime("%Y%m%d"))
        except Exception as e:
            print("  %-10s 失败 %s %s" % (code, type(e).__name__, str(e)[:80]))
            continue
        if df is None or not len(df):
            print("  %-10s 空" % code)
            continue
        rows = []
        for _, r in df.iterrows():
            rows.append([str(r["trade_date"]), float(r["close"]),
                         float(r.get("high") or r["close"]), float(r.get("amount") or 0)])
        rows.sort()
        p = os.path.join(DATA, "index_weekly_%s.json" % code.replace(".", "_"))
        json.dump({"code": code, "src": "tushare_relay:index_weekly", "rows": rows},
                  open(p + ".tmp", "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(p + ".tmp", p)
        print("  %-10s n=%-5d %s → %s ✓" % (code, len(rows), rows[0][0], rows[-1][0]))


if __name__ == "__main__":
    main()
