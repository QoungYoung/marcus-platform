# -*- coding: utf-8 -*-
"""「疑似延伸/自救段」影子标签 —— 三指标 + **客观验收**（2026-10-21 ✓）。

背景：用户问「我们能真的识别出延伸浪吗」。答案是不能 ✗（缺完整计数 / 缺度 / 缺盘口）。
退而求其次：**不给它起浪号，只标记"不像趋势"的状态** ✓，并且用**物理事实**验收

  —— 标记之后 5/10 日，是否真的**没走出趋势**（|累计涨跌| 小 ∧ 未创 20 日新高/新低）✓
  —— 这样就不依赖他的命名/操作当 ground truth（E1/E1b 就是这么失败的 ✗）。

三条判据（**事前声明 ✓ 不许看到结果再调**）：
  ① 权重被压：银行指数 5 日相对上证的强弱 ≤ -1.0%（他 10-21 原话「用银行压着上证」✓）
  ② 区间横盘 + 量能温和：20 日振幅 ≤ 6% ∧ 当日成交额/前 20 日均额 ∈ [0.6, 1.4]
  ③ 结构未出：无底分型 ∧ 无红三兵 ∧ 无岛反 ∧ 无顶分破位

验收：
  · 命中率 = 标记日中「事后 5 日没走出趋势」的比例 ✓
  · **基线** = 全部日子里同口径的比例 ✓（必须打赢基线才叫有用 ✓）
  · 另报 10 日口径 ✓

用法::
    python jobs/eval_sideways_regime.py            # 上证
"""
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query   # noqa: E402

START, END = "20240101", "20261021"
REL_TH, AMP_TH, VOL_LO, VOL_HI = -1.0, 6.0, 0.6, 1.4     # 事前声明 ✓
HOLD = (5, 10)
TREND_TH = 3.0                                            # |累计涨跌| > 3% 视为"走出趋势" ✗


def load(code, fn="index_daily"):
    df = relay_query(fn, ts_code=code, start_date=START, end_date=END)
    rows = []
    for _, r in (df.iterrows() if df is not None else []):
        rows.append({"d": str(r["trade_date"]), "o": float(r["open"]), "h": float(r["high"]),
                     "l": float(r["low"]), "c": float(r["close"]), "amt": float(r.get("amount") or 0)})
    return sorted(rows, key=lambda x: x["d"])


def pct(rows, i, n):
    if i - n < 0:
        return None
    return (rows[i]["c"] / rows[i - n]["c"] - 1) * 100


def bottom_fractal(rows, i):
    return i >= 2 and rows[i - 1]["l"] < rows[i]["l"] and rows[i - 1]["l"] < rows[i - 2]["l"] \
        and rows[i]["c"] > rows[i - 2]["c"]


def three_soldiers(rows, i):
    return i >= 2 and all(rows[j]["c"] > rows[j]["o"] for j in (i - 2, i - 1, i)) \
        and rows[i]["c"] > rows[i - 1]["c"] > rows[i - 2]["c"]


def island_reversal(rows, i):
    return i >= 2 and rows[i - 1]["h"] < rows[i - 2]["l"] and rows[i]["l"] > rows[i - 1]["h"]


def top_break(rows, i):
    # 前 3 根里出现"顶分"（中间最高）且今日收盘跌破该顶分的低点 ⇒ 结构向下成立
    for k in range(i - 3, i - 0):
        if k - 1 < 0 or k + 1 >= len(rows):
            continue
        if rows[k]["h"] > rows[k - 1]["h"] and rows[k]["h"] > rows[k + 1]["h"] and rows[i]["c"] < rows[k]["l"]:
            return True
    return False


def main():
    sh = load("000001.SH")
    bk = load("399986.SZ")
    if len(sh) < 60 or len(bk) < 60:
        print("数据不足 ✗", len(sh), len(bk))
        return 1
    bmap = {r["d"]: r for r in bk}
    print("样本：%s ~ %s（上证 %d 根 ✓ 银行 %d 根 ✓）" % (sh[0]["d"], sh[-1]["d"], len(sh), len(bk)))
    print("判据（事前声明）：银行5日相对强弱 ≤ %.1f%% ∧ 20日振幅 ≤ %.0f%% ∧ 量比∈[%.1f,%.1f] ∧ 四结构全否"
          % (REL_TH, AMP_TH, VOL_LO, VOL_HI))
    print("验收：{5,10} 日后 |累计涨跌| ≤ %.0f%% 且期间未创 20 日新高/新低 ⇒ 算「没走出趋势」✓\n"
          % TREND_TH)

    flags, base = [], []
    for i in range(25, len(sh) - max(HOLD)):
        d = sh[i]["d"]
        b = bmap.get(d)
        if not b:
            continue
        r5s, r5b = pct(sh, i, 5), pct(bk, [x["d"] for x in bk].index(d), 5)
        if r5s is None or r5b is None:
            continue
        rel = r5b - r5s
        lo = min(x["l"] for x in sh[i - 19:i + 1])
        hi = max(x["h"] for x in sh[i - 19:i + 1])
        amp = (hi / lo - 1) * 100 if lo else 0.0
        amt20 = statistics.mean([x["amt"] for x in sh[i - 20:i]])
        volx = sh[i]["amt"] / amt20 if amt20 else 0.0
        struct = (bottom_fractal(sh, i) or three_soldiers(sh, i) or island_reversal(sh, i) or top_break(sh, i))
        ok = (rel <= REL_TH) and (amp <= AMP_TH) and (VOL_LO <= volx <= VOL_HI) and (not struct)
        base.append(i)
        if ok:
            flags.append(i)

    def outcome(i, n):
        if i + n >= len(sh):
            return None
        ret = (sh[i + n]["c"] / sh[i]["c"] - 1) * 100
        win = sh[i + 1:i + n + 1]
        hi20 = max(x["h"] for x in sh[max(0, i - 19):i + 1])
        lo20 = min(x["l"] for x in sh[max(0, i - 19):i + 1])
        broke = any(x["h"] > hi20 or x["l"] < lo20 for x in win)
        return abs(ret) <= TREND_TH and not broke

    print("被标记 %d 天 / 可比样本 %d 天（%.1f%%）" % (len(flags), len(base), 100.0 * len(flags) / max(len(base), 1)))
    for n in HOLD:
        f_ok = [outcome(i, n) for i in flags]
        b_ok = [outcome(i, n) for i in base]
        f_ok = [x for x in f_ok if x is not None]
        b_ok = [x for x in b_ok if x is not None]
        if not f_ok or not b_ok:
            print("  %2d 日：样本不足" % n)
            continue
        fr, br = 100.0 * sum(f_ok) / len(f_ok), 100.0 * sum(b_ok) / len(b_ok)
        print("  %2d 日：标记命中 %.0f%%（%d/%d）｜基线 %.0f%%（%d/%d）｜lift %.2f%s"
              % (n, fr, sum(f_ok), len(f_ok), br, sum(b_ok), len(b_ok), fr / br if br else 0,
                 "  ← 打赢基线 ✓" if fr > br + 3 else "  ← 没打赢 ✗"))
    print("\n最近 12 个标记日：", [sh[i]["d"] for i in flags[-12:]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
