# -*- coding: utf-8 -*-
"""结构信号是**指数级**还是**板块/个股级**？（2026-10-21 ✓）

背景：用户问「低位顶分破位和底分型是不是板块或者个股信号？」—— 指数级统计里它们是无效/反向的 ✗，
        但**结构可能是横截面信号**（同一时点选股/选板块 ✓ 而不是择时 ✓）。

设计（事前声明 ✓）：
  · 三层样本：① 指数（上证/创业板/科创50）② 中证一级行业（000928~000937）③ 个股样本 200 只（确定性抽取）
  · 判据与线上一致 ✓：底分型 / 低位顶分破位（其余两种样本太稀 ✓）
  · 收益口径：**10 日收益 − 同日该层全样本均值** ✓（= 横截面超额 ✓ 消掉大盘 beta ✓）
  · 判定：超额均值 > 0 且胜率（超额>0 占比）> 55% ⇒ ★有效 ✓；< 45% ⇒ ✗反向
  · 另报组内基线的"超额>0 占比"= 50% 附近（横截面天然 50% ✓）

用法：python jobs/eval_structure_levels.py [--stocks 200] [--start 20160101]
"""
import argparse
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, "core")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tushare_relay import relay_query   # noqa: E402

HOLD = 10


def load(code, start, fn="index_daily"):
    try:
        df = relay_query(fn, ts_code=code, start_date=start, end_date="20261031")
    except Exception:
        return []
    rows = []
    for _, r in (df.iterrows() if df is not None else []):
        rows.append({"d": str(r["trade_date"]), "h": float(r["high"]), "l": float(r["low"]),
                     "o": float(r["open"]), "c": float(r["close"])})
    return sorted(rows, key=lambda x: x["d"])


def bottom_fractal(rows, i):
    if i < 2:
        return False
    a, b, c = rows[i - 2], rows[i - 1], rows[i]
    return b["l"] < a["l"] and b["l"] < c["l"] and c["c"] > a["c"]


def top_break_low(rows, i):
    if i < 4:
        return False
    hi60 = max(x["h"] for x in rows[max(0, i - 59):i + 1])
    if not hi60 or (rows[i]["c"] / hi60 - 1) * 100 > -5.0:
        return False
    for k in range(i - 3, i):
        if k - 1 < 0 or k + 1 >= len(rows):
            continue
        if rows[k]["h"] > rows[k - 1]["h"] and rows[k]["h"] > rows[k + 1]["h"] and rows[i]["c"] < rows[k]["l"]:
            return True
    return False


def scan(entities, start, label):
    """entities: [(code, name, fn)] → 每层的结构日 + 同日横截面超额 ✓"""
    data = {}
    for code, name, fn in entities:
        rs = load(code, start, fn)
        if len(rs) >= 120:
            data[name] = rs
    if len(data) < 3:
        print("%s：有效标的不足 ✗ %d" % (label, len(data)))
        return {}
    # 同日横截面：date -> 10 日收益 的均值
    fwd = {}
    for name, rs in data.items():
        idx = {r["d"]: i for i, r in enumerate(rs)}
        for i, r in enumerate(rs):
            if i + HOLD >= len(rs):
                continue
            fwd.setdefault(r["d"], []).append((rs[i + HOLD]["c"] / r["c"] - 1) * 100)
    day_mean = {d: statistics.mean(v) for d, v in fwd.items() if v}
    out = {}
    for name, rs in data.items():
        for i, r in enumerate(rs):
            if i + HOLD >= len(rs) or r["d"] not in day_mean:
                continue
            excess = (rs[i + HOLD]["c"] / r["c"] - 1) * 100 - day_mean[r["d"]]
            for kind, hit in (("底分型", bottom_fractal(rs, i)), ("低位顶分破位", top_break_low(rs, i))):
                if hit:
                    out.setdefault(kind, []).append(excess)
    print("%s：标的 %d 个 ✓（%s）" % (label, len(data), list(data)[:3]))
    for kind, ex in sorted(out.items()):
        n = len(ex)
        win = 100.0 * sum(1 for x in ex if x > 0) / n if n else 0
        print("   %-12s n=%-4d 10日横截面超额均值 %+.3f%% ｜ 超额胜率 %.0f%% ｜ %s" % (
            kind, n, statistics.mean(ex) if n else 0, win,
            "★ 有效 ✓" if (win >= 55 and statistics.mean(ex) > 0) else
            ("✗ 反向" if win <= 45 else "≈ 随机 ✓ 无优势")))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20160101")
    ap.add_argument("--stocks", type=int, default=200)
    args = ap.parse_args()

    print("判据：底分型 / 低位顶分破位 ✓ ｜ 收益：%d 日**横截面超额**（减同日同层均值 ✓）\n" % HOLD)
    scan([("000001.SH", "上证", "index_daily"), ("399006.SZ", "创业板", "index_daily"),
          ("000688.SH", "科创50", "index_daily")], args.start, "① 指数级")
    print()
    scan([("%s.SH" % c, "行业%s" % c, "index_daily") for c in
          ("000928", "000929", "000930", "000931", "000932", "000933", "000934", "000935", "000936", "000937")],
         args.start, "② 中证一级行业")
    print()
    # 个股样本：确定性抽取（上市早 + 代码排序 ✓ 避免未来函数 ✓）
    try:
        df = relay_query("stock_basic", exchange="", list_status="L", fields="ts_code,name,list_date")
        cand = sorted(str(r["ts_code"]) for _, r in df.iterrows()
                      if str(r.get("list_date") or "99999999") <= "20160101"
                      and not str(r["ts_code"]).startswith(("688", "30", "8", "4")))
    except Exception as e:
        print("取股票列表失败 ✗ %s" % str(e)[:100])
        return 1
    sample = cand[: args.stocks]
    print("个股样本：%d 只（主板 ✓ 上市早于 2016 ✓ 代码排序确定性抽取 ✓）" % len(sample))
    scan([(c, c, "daily") for c in sample], args.start, "③ 个股级")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
