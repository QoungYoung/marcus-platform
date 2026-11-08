# -*- coding: utf-8 -*-
"""B反 多标的监测（2026-10-22 ✓ P1）。

为什么：`wave_ladder.b_rebound` 只算**上证** ✗，而上证是"结果"、主线 ETF 常先见底 ✓
  ⇒ 实测 2026-06 那波：上证 06-08 见底、06-15 才亮 B反 ✓；而 588170 在 06-09 起涨 ✓
  ⇒ 用**各标的自己**的 b_rebound 可以更早 ✓（本脚本负责量化"早几天"）

判据与 `apps/main_line/wave_ladder.py::features` 完全一致（不另创 ✗）：
    b_rebound = 收盘/20日低 − 1 ≥ 3%  ∧  收盘 < 20日高  ∧  收盘 < 前 60 日高
（前 60 日高 = 不含当日，对应 wave_ladder 的 hi60_prev ✓）

输出：`data/breb_multi.json`（各标的当前状态 + 首次亮灯日期 ✓）
用法::
    python jobs/breb_multi.py                 # 算今天 ✓ 写文件 ✓
    python jobs/breb_multi.py --lead 20260615 20260630   # 回测某窗口：各标的谁先亮 ✓
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query   # noqa: E402

DATA = os.environ.get("DATA_DIR", "").strip() or "data"
OUT = os.path.join(DATA, "breb_multi.json")
TH = 0.03
SYMBOLS = [("000001.SH", "上证指数", "index_daily"), ("588170.SH", "科创半导体设备", "fund_daily"),
           ("512480.SH", "半导体ETF", "fund_daily"), ("002156.SZ", "通富微电", "daily"),
           ("600584.SH", "长电科技", "daily")]


def bars(code, fn, start="20250101", end="20261231"):
    df = relay_query(fn, ts_code=code, start_date=start, end_date=end)
    if df is None or not len(df):
        return []
    return sorted([{"d": str(r["trade_date"]), "c": float(r["close"]),
                    "h": float(r["high"]), "l": float(r["low"])} for _, r in df.iterrows()],
                  key=lambda x: x["d"])


def breb_flags(rs):
    """→ {日期: bool}（与 wave_ladder 同口径 ✓ 只用当日及之前 ✓ 无未来函数 ✓）"""
    out = {}
    for i, x in enumerate(rs):
        if i < 60:
            out[x["d"]] = False
            continue
        lo20 = min(y["l"] for y in rs[i - 19:i + 1])
        hi20 = max(y["h"] for y in rs[i - 19:i + 1])
        hi60_prev = max(y["h"] for y in rs[i - 60:i])
        out[x["d"]] = bool(lo20 and (x["c"] / lo20 - 1) >= TH and x["c"] < hi20 and x["c"] < hi60_prev)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lead", nargs=2, metavar=("D0", "D1"), help="回测某窗口内各标的首次亮灯日 ✓")
    args = ap.parse_args()

    cache = {}
    for code, name, fn in SYMBOLS:
        cache[code] = (name, bars(code, fn))
    if args.lead:
        d0, d1 = args.lead
        print("窗口 {} ~ {} ｜ 各标的 B反 **首次亮灯** ✓".format(d0, d1))
        firsts = {}
        for code, (name, rs) in cache.items():
            flags = breb_flags(rs)
            # ★ 取**由假变真**的那一天（不是"窗口内第一个为真" ✗ 那样会误取窗口起点 ✓）
            ds = sorted(flags)
            f = next((ds[k] for k in range(1, len(ds))
                      if d0 <= ds[k] <= d1 and flags[ds[k]] and not flags[ds[k-1]]), None)
            firsts[name] = f
            print("   {:<16} {}".format(name, f or "未亮 ✗"))
        base = firsts.get("上证指数")
        if base:
            for name, f in firsts.items():
                if f and f != base:
                    # 用交易日序号比较 ✓
                    rs = cache["000001.SH"][1]
                    order = [x["d"] for x in rs]
                    gap = order.index(base) - order.index(f) if (f in order and base in order) else None
                    if gap is not None and gap != 0:
                        print("   ⇒ {} 比上证{} {} 个交易日".format(
                            name, "早" if gap > 0 else "晚", abs(gap)))
        return 0

    # 当日状态
    today = {}
    for code, (name, rs) in cache.items():
        flags = breb_flags(rs)
        ds = sorted(flags)
        last = ds[-1] if ds else None
        on = bool(flags.get(last))
        # 最近一次"连续为真"的起点 ✓
        start = None
        if on:
            k = len(ds) - 1
            while k > 0 and flags[ds[k - 1]]:
                k -= 1
            start = ds[k]
        today[name] = {"code": code, "date": last, "close": rs[-1]["c"] if rs else None,
                       "b_rebound": on, "since": start}
        print("  {:<16} {} 收 {:.3f} ｜ B反 {}（自 {} 起）".format(
            name, last, rs[-1]["c"] if rs else 0, "**是 ✓**" if on else "否 ✗", start or "-"))
    try:
        os.makedirs(DATA, exist_ok=True)
        json.dump(today, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写 {}".format(OUT))
    except Exception as e:
        print("\n写文件失败: {}".format(str(e)[:80]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
