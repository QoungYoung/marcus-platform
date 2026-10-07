# -*- coding: utf-8 -*-
"""跨"代"回测对比：用**同一套重建口径**把各次年跑的第一阶段收益摆在一起。

为什么需要它：看板只显示当前这一跑的账户（PG 每次 reset 都会覆盖），而"上一跑收益多少"只留在
`_summary_prev_*/prod_<day>.json` 里。本脚本把每份日终快照里的**累计成交**重放成持仓，
再按**同一天的日线收盘**重估市值 → 得到可逐代对比的 equity 曲线（口径统一，跨代可比）。

口径（三处必须一致）：
  · 现金：直接用该日快照 `account[0].available_cash`（各代都是同一个 stock 模拟盘，同一套费用）
  · 市值：Σ 持仓量 × **当日在 bars.sqlite 的收盘**；当日无收盘 → 用该标的最近一次已知收盘（沿用不消失）
  · 收益：equity / initial - 1；同时给出"已实现"（卖出笔数按 reason 分类）与换手（买卖笔数）

用法：.venv/bin/python jobs/bt_cmp_gens.py [--days 20260105:20260204] [--json out.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BARS = os.path.join(REPO, "data/_bt_full/bars.sqlite")


def to_ts_code(sym: str) -> str:
    s = str(sym or "").upper().strip()
    if len(s) >= 8 and s[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (s[2:], s[:2])
    return s


def load_closes(symbols, days):
    """{(ts_code, day): close} + 每标的的升序 (day, close) 供"沿用最近收盘"。"""
    con = sqlite3.connect(BARS)
    cur = con.cursor()
    out, series = {}, defaultdict(list)
    for code in {to_ts_code(s) for s in symbols}:
        for d, c in cur.execute(
                "SELECT trade_date, close FROM bars WHERE ts_code=? AND close>0 ORDER BY trade_date", (code,)):
            out[(code, d)] = float(c)
            series[code].append((d, float(c)))
    con.close()
    return out, series


def mark(series, code, day):
    """该日收盘；无则沿用最近一次（避免"当天没数据就当成 0 市值"的假跌）。"""
    if (code, day) in series[0]:
        return series[0][(code, day)]
    prev = None
    for d, c in series[1].get(code, []):
        if d <= day:
            prev = c
        else:
            break
    return prev


def rebuild(path_glob):
    files = sorted(glob.glob(path_glob))
    if not files:
        return None
    # 先收齐符号，再一次性取收盘
    raws = []
    syms = set()
    for f in files:
        j = json.load(open(f, encoding="utf-8"))
        day = str(j.get("day"))
        tr = j.get("trades") or []
        acc = (j.get("account") or [{}])[0]
        raws.append((day, tr, float(acc.get("available_cash") or 0.0), j))
        for t in tr:
            syms.add(t.get("symbol"))
    closes = load_closes(syms, None)
    out, pos = [], {}
    for day, tr, cash, j in raws:
        pos = {}
        reason_by = Counter()
        for t in tr:                                     # 累计成交 → 重放成持仓
            s = t.get("symbol")
            v = int(t.get("volume") or 0)
            if str(t.get("direction")) == "买入":
                pos[s] = pos.get(s, 0) + v
            else:
                pos[s] = pos.get(s, 0) - v
                reason_by[str(t.get("reason") or "?")[:18]] += 1
        pos = {k: v for k, v in pos.items() if v > 0}
        mv, miss = 0.0, []
        for s, v in pos.items():
            c = mark(closes, to_ts_code(s), day)
            if c is None:
                miss.append(s)
            else:
                mv += v * c
        out.append({"day": day, "cash": cash, "mv": mv, "equity": cash + mv,
                    "n_pos": len(pos), "n_trades": len(tr),
                    "sell_reasons": dict(reason_by), "missing_mark": miss})
    return out


GENS = [
    ("旧年跑(prod链, 修复前)", "data/_bt_year/_summary_prev_20260917_1539/prod_2026*.json"),
    ("gen=4(止损空转)", "data/_bt_year/_summary_prev_20260917_1714/prod_2026*.json"),
    ("gen=5(网关未接)", "data/_bt_year/_summary_prev_20260917_1721/prod_2026*.json"),
    ("gen=6(当前, 全修复)", "data/_bt_year/_summary/prod_2026*.json"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="20260105:20260204")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    d0, d1 = a.days.split(":")
    res = {}
    for name, g in GENS:
        cur = rebuild(os.path.join(REPO, g))
        if not cur:
            print("!! 无数据:", name)
            continue
        cur = [c for c in cur if d0 <= c["day"] <= d1]
        res[name] = cur
    print("══ 第一阶段逐代对比（口径：现金=当日快照；市值=持仓×当日收盘；%s→%s）" % (d0, d1))
    for name, cur in res.items():
        if not cur:
            continue
        eq = [c["equity"] for c in cur]
        peak, mdd = eq[0], 0.0
        for e in eq:
            peak = max(peak, e)
            mdd = min(mdd, (e / peak - 1) * 100)
        print("\n── %s（%d 天，%s→%s）" % (name, len(cur), cur[0]["day"], cur[-1]["day"]))
        print("   期末 equity=%10.2f  收益=%+6.2f%%  最大回撤=%.2f%%  期末持仓=%d  累计成交=%d"
              % (cur[-1]["equity"], (cur[-1]["equity"] / 250000 - 1) * 100, mdd,
                 cur[-1]["n_pos"], cur[-1]["n_trades"]))
        for c in cur:
            print("     %s cash=%9.2f mv=%9.2f equity=%10.2f (%+6.2f%%) pos=%2d trades=%3d %s"
                  % (c["day"], c["cash"], c["mv"], c["equity"],
                     (c["equity"] / 250000 - 1) * 100, c["n_pos"], c["n_trades"],
                     ("缺价:%s" % ",".join(c["missing_mark"][:3])) if c["missing_mark"] else ""))
    if a.json:
        json.dump(res, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n已写出", a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
