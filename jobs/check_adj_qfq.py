# -*- coding: utf-8 -*-
"""自检：前复权取数（P0-1 ✓ 2026-10-22）—— 四条断言全过才 exit 0 ✓。

为什么要有它（用户 2026-10-22）：
  · `512480.SH` 2026-07-03 做 1:2 份额折算；`588170.SH` 2026-07-06 做 1:3 份额折算；
  · 用**原始价**算日涨跌 ⇒ 那是 -50% / -67% 的**假暴跌** ✗ ⇒ 误判减仓 ✗；
  · 复权后同一位置必须看不到假暴跌 ✓，且**最新一日**收盘价要与原始价完全相等 ✓（前复权末端不缩放 ✓）。
  以后改 `core/adj_quote.py` 先跑这个脚本 ✓（失败就别上线 ✗）。

用法::
    python jobs/check_adj_qfq.py        # 全过 ⇒ 退出码 0 ✓；任一失败 ⇒ 1 ✗
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from adj_quote import qfq_bars    # noqa: E402

FIELDS = ("d", "o", "h", "l", "c", "o_raw", "h_raw", "l_raw", "c_raw", "amt")
SPLITS = (("512480.SH", "20260625", "20260710", "20260703", 1.9638, 3.9276),
          ("588170.SH", "20260629", "20260710", "20260706", 1.0, 3.0))
MAX_PCT = 12.0        # 复权后单日涨跌上限（ETF 有涨跌停 ✓ 正常绝到不了 12% ✗）
MIN_RAW_JUMP = 40.0   # 原始价在折算日必须看到 ≥40% 的跳变 ✓（证明这条断言真在测东西 ✓）


def find(bars, day):
    for i, b in enumerate(bars):
        if b["d"] == day:
            return i
    return -1


def pct(prev, cur):
    return (cur / prev - 1.0) * 100.0 if prev else 0.0


def main():
    fails = []

    for code, start, end, day, f_old, f_new in SPLITS:
        bars = qfq_bars(code, start, end)
        i = find(bars, day)
        print("【{}】{}~{} 共 {} 根 ✓ 折算日 {}".format(code, start, end, len(bars), day))
        if i < 1:
            fails.append("{}：折算日 {} 缺数据 ✗（或没有前一根 ✗）".format(code, day))
            print("  ⇒ ✗ 折算日缺数据")
            continue
        adj_pct = pct(bars[i - 1]["c"], bars[i]["c"])
        raw_pct = pct(bars[i - 1]["c_raw"], bars[i]["c_raw"])
        print("  前一日 {} 收 {:.4f}（原始 {:.4f}）⇒ 折算日收 {:.4f}（原始 {:.4f}）".format(
            bars[i - 1]["d"], bars[i - 1]["c"], bars[i - 1]["c_raw"],
            bars[i]["c"], bars[i]["c_raw"]))
        print("  复权后日涨跌 {:+.2f}% ✓（要求 |x| < {:.0f}%）｜ 原始价日涨跌 {:+.2f}% ✗（要求 <-{:.0f}%）".format(
            adj_pct, MAX_PCT, raw_pct, MIN_RAW_JUMP))
        print("  因子对照：折算前 {:.4f} → 折算后 {:.4f}（理论原始跳变 {:+.2f}%）".format(
            f_old, f_new, (f_old / f_new - 1.0) * 100.0))
        if abs(adj_pct) >= MAX_PCT:
            fails.append("{}：复权后折算日仍跳 {:+.2f}% ✗".format(code, adj_pct))
        if raw_pct > -MIN_RAW_JUMP:
            fails.append("{}：原始价折算日只跳 {:+.2f}% ✗（对照失效 ✗）".format(code, raw_pct))

    # ③ 个股口径（daily + adj_factor ✓）
    stock = qfq_bars("002156.SZ", "20261001", "20261022")
    print("【002156.SZ】个股 20261001~20261022 共 {} 根 ✓（要求 ≥10 ✓）".format(len(stock)))
    if len(stock) < 10:
        fails.append("002156.SZ 只回了 {} 根 ✗（要求 ≥10 ✗）".format(len(stock)))
    for b in stock:
        miss = [k for k in FIELDS if b.get(k) is None]
        if miss:
            fails.append("002156.SZ {} 字段缺失 {} ✗".format(b.get("d"), miss))
            break
    print("  字段齐全 {} ✓ 首 {} 尾 {}".format(
        len(FIELDS), stock[0]["d"] if stock else "-", stock[-1]["d"] if stock else "-"))

    # ④ 末端不缩放 ✓（最新一日 c == c_raw ✓）
    for code, start, end, _day, _fo, _fn in SPLITS + (("002156.SZ", "20261001", "20261022", "", 0, 0),):
        bars = qfq_bars(code, start, end)
        if not bars:
            fails.append("{} 取数空 ✗（末端断言无法检验 ✗）".format(code))
            continue
        gap = abs(bars[-1]["c"] - bars[-1]["c_raw"])
        print("【{}】最新一日 {} 复权收 {:.6f} ｜ 原始收 {:.6f} ｜ 差 {:.2e} ✓".format(
            code, bars[-1]["d"], bars[-1]["c"], bars[-1]["c_raw"], gap))
        if gap > 1e-9:
            fails.append("{} 末端被缩放 ✗ 差 {:.6e} ✗".format(code, gap))

    if fails:
        print("\n✗ 自检失败 {} 条：".format(len(fails)))
        for x in fails:
            print("  · " + x)
        return 1
    print("\n✓ 四条断言全过（假暴跌已消除 ✓ 末端不缩放 ✓ 个股口径 ✓ 字段齐全 ✓）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
