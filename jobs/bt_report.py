#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bt_report.py — 看**回测收益**（净值曲线 / 分主题 / 分腿型）。

两种模式（自动判断）：
  ① **正式结果**：存在 `--account` 指定的账户结果（`jobs/bt_account.py` 产出）时，打印该口径的
     期末净值、区间收益、最大回撤、逐日净值、成交笔数与费用、分主题/分腿型收益；
  ② **粗估预览**（跑批进行中、账户层还没跑）：用当日腿 + 本地日线做"等权、次日开盘买入、持有到最新价"的
     粗估收益。**明确标注为粗估**：不含 T+1 / 整手 / 涨跌停 / 触发时机 / 费用 / 护栏，仅供方向参考。

用法（仓库根）：
    .venv/bin/python jobs/bt_report.py                      # 自动：有正式结果用正式，没有就粗估
    .venv/bin/python jobs/bt_report.py --rough              # 只做粗估
    .venv/bin/python jobs/bt_report.py --account data/_bt_year/_summary/account_hold.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_env  # noqa: E402


def ts_code(sym: str) -> str:
    s = str(sym).upper()
    return "%s.%s" % (s[2:], s[:2]) if len(s) == 8 and s[:2] in ("SH", "SZ", "BJ") else s


def load_legs(root: str):
    """逐日腿（两条路径合并）→ {day: [ {symbol, theme, type, src} ]}"""
    out = {}
    for p in sorted(glob.glob(os.path.join(root, "2026*"))):
        d = os.path.basename(p)
        if not (len(d) == 8 and d.isdigit()):
            continue
        rows = []
        for fn, src in (("legs_switch.jsonl", "switch_0818"), ("legs.jsonl", "arm_0920")):
            f = os.path.join(p, fn)
            if not os.path.exists(f):
                continue
            for ln in open(f, encoding="utf-8"):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    j = json.loads(ln)
                except Exception:
                    continue
                sym = str(j.get("symbol") or "").upper()
                if sym:
                    rows.append({"symbol": sym, "theme": j.get("theme") or "(无主题)",
                                 "type": str(j.get("type") or "buy_253/254"), "src": src})
        if rows:
            out[d] = rows
    return out


def price(sq, sym: str, day: str, field: str = "close"):
    r = sq.execute("SELECT %s FROM bars WHERE ts_code=? AND trade_date=?" % field, (ts_code(sym), day)).fetchone()
    return float(r[0]) if r and r[0] else None


def next_day(sq, day: str):
    r = sq.execute("SELECT min(trade_date) FROM bars WHERE trade_date>?", (day,)).fetchone()
    return r[0] if r and r[0] else None


def latest_day(sq):
    r = sq.execute("SELECT max(trade_date) FROM bars").fetchone()
    return r[0] if r and r[0] else None


def rough_report(root: str, bars_db: str, initial: float = 250000.0, budget: float = 25000.0,
                 entry_mode: str = "same_day_close"):
    """粗估：**组合口径**（逐日腿 → 固定单笔预算买入 → 逐日 mark-to-market），并给单笔统计。

    entry_mode：`same_day_close`（默认，腿当日收盘成交——腿在 09:20 布好后当日盘中触发，最接近现实）
                `next_open`（次一交易日开盘成交——更保守）
    ⚠️ 仍是粗估：不含 T+1 / 整手 / 涨跌停 / 触发是否真的命中 / 卖出（只买不卖）/ 护栏；费用只算买入单边。
    """
    import importlib
    sys.path.insert(0, os.path.join(bt_env.REPO, "jobs"))
    acct_mod = importlib.import_module("bt_account")
    fee_b, fee_s, _prof = acct_mod.fee_rates()

    legs = load_legs(root)
    if not legs:
        print("还没有任何腿（跑批刚起步）→ 暂无收益可看。")
        return 0
    sq = sqlite3.connect(bars_db)
    # ⚠️ **不能用"库里最新那天"** 做 mark-to-market 末端：跑批才跑到第 N 天时，那等于拿未来价格估值（前视）。
    #    只到"跑批已处理到的最后一天"（有 `_seed.json` 的沙箱日；退化为最后一个有腿的日）。
    processed = []
    for d in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        pdir = os.path.join(root, d)
        if len(d) == 8 and d.isdigit() and os.path.exists(os.path.join(pdir, "_seed.json")):
            processed.append(d)
    last = max(processed) if processed else (max(legs) if legs else latest_day(sq))
    per, by_theme, by_type, n = [], {}, {}, 0
    acct = acct_mod.Account(initial, fee_b, fee_s)
    pending_buys, bought, trades, blocked = [], [], [], {}
    curve = []
    all_days = [r[0] for r in sq.execute("SELECT DISTINCT trade_date FROM bars ORDER BY trade_date")]
    first_leg = min(legs)
    window = [d for d in all_days if first_leg <= d <= last]

    for day in window:
        for leg in legs.get(day, []):
            sym = leg["symbol"]
            fill_day = day if entry_mode == "same_day_close" else (next_day(sq, day) or "")
            if not fill_day or fill_day > last or fill_day not in window:
                # 次开模式：在 fill_day 当天成交 → 这里按窗口顺序到那天再买
                if entry_mode == "next_open":
                    continue
            px = price(sq, sym, fill_day, "close" if entry_mode == "same_day_close" else "open") \
                or price(sq, sym, fill_day, "close")
            if not px:
                continue
            if any(t.get("symbol") == sym and t.get("day") == day for t in pending_buys):
                continue
            pending_buys.append({"symbol": sym, "day": day, "fill_day": fill_day, "price": px,
                                 "theme": leg["theme"], "type": leg["type"]})
        # 到 fill_day 的买单执行
        for b in [x for x in pending_buys if x["fill_day"] == day]:
            sym = b["symbol"]
            # 单标的单日买入笔数上限（t_gateway.MAX_DAILY_BUY_LEGS=2；同日同标的已去重 → 实际 ≤1）
            if sum(1 for t2 in trades if t2["symbol"] == sym and t2["day"] == day) >= 2:
                continue
            # 跌停禁买（t_gateway._near_limit_down：现价 ≤ 昨收×(1−9.8%)）
            pre = price(sq, sym, day, "pre_close") or 0.0
            if acct_mod.near_limit_down(b["price"], pre):
                blocked["near_limit_down"] = blocked.get("near_limit_down", 0) + 1
                continue
            vol = int(min(budget, acct.cash * 0.98) / max(b["price"], 0.01) / 100) * 100
            r = acct.buy(sym, b["price"], vol, day) if vol > 0 else None
            bought.append(b)
            if r:
                trades.append({"day": day, "symbol": b["symbol"], "price": b["price"], "vol": vol,
                               "theme": b["theme"], "type": b["type"], "fee": r["fee"]})
        closes = {}
        for sym in list(acct.pos):
            c = price(sq, sym, day, "close")
            if c:
                closes[sym] = c
        mv = sum(acct.pos[s2]["vol"] * closes.get(s2, acct.pos[s2]["cost"]) for s2 in acct.pos)
        curve.append({"day": day, "cash": round(acct.cash, 2), "mv": round(mv, 2),
                      "equity": round(acct.cash + mv, 2), "n_pos": len(acct.pos)})

    # 单笔统计（对照：等权、持有到最新收盘）
    for day in sorted(legs):
        seen = set()
        for leg in legs[day]:
            sym = leg["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            fd = day if entry_mode == "same_day_close" else (next_day(sq, day) or "")
            e = price(sq, sym, fd, "close" if entry_mode == "same_day_close" else "open") if fd else None
            x = price(sq, sym, last, "close") if e else None
            if not e or not x:
                continue
            ret = x / e - 1
            per.append(ret)
            by_theme.setdefault(leg["theme"], []).append(ret)
            by_type.setdefault(leg["type"], []).append(ret)
            n += 1
    sq.close()
    if not per:
        print("腿部存在但取不到价格（检查本地 bars 覆盖）→ 无法粗估。")
        return 0
    avg = sum(per) / len(per)
    win = sum(1 for r in per if r > 0) / len(per)
    print("== 粗估收益（**不是**最终口径；它是**上限**：假设每笔腿当天都成交）")
    if curve:
        eq = curve[-1]["equity"]
        peak, mdd = curve[0]["equity"], 0.0
        for c in curve:
            peak = max(peak, c["equity"])
            mdd = min(mdd, c["equity"] / peak - 1)
        print("   **截止当前（跑批已处理到 %s）：总收益 %+.2f%%**（初始 %.0f → 净值 %.2f；最大回撤 %.2f%%）"
              % (curve[-1]["day"], (eq / initial - 1) * 100, initial, eq, mdd * 100))
        print("   组合：现金 %.0f + 持仓市值 %.0f（%d 只）；已投入单笔预算 %.0f/笔，费用(买入单边) %.2f"
              % (curve[-1]["cash"], curve[-1]["mv"], curve[-1]["n_pos"], budget, acct.commission))
        step = max(len(curve) // 8, 1)
        print("   净值轨迹（每 %d 个交易日取样）：" % step)
        _show = curve[::step][:9]
        if _show[-1] is not curve[-1]:
            _show = _show + [curve[-1]]
        for c in _show:
            print("     %s  净值 %10.2f  (%+.2f%%)  持仓 %d" % (c["day"], c["equity"],
                                                                 (c["equity"] / initial - 1) * 100, c["n_pos"]))
    print("\n   单笔口径：样本 %d 笔腿（%d 个交易日；%s 成交 → 持有至 %s 收盘）"
          % (n, len(legs), "腿当日收盘" if entry_mode == "same_day_close" else "次一交易日开盘", last))
    print("   等权平均单笔收益：%+.2f%%   胜率：%.0f%%" % (avg * 100, win * 100))
    print("")
    print("   口径清单（粗估预览）——")
    print("     ✅ 已含：100 股整手、单笔 2.5 万预算（生产试仓档）、买入单边费用 0.0396%、现金约束、")
    print("             单标的单日买入 ≤2 笔（同标的同日去重后实际 ≤1）、跌停禁买（现价 ≤ 昨收×0.902）")
    print("     ❌ 未含（必须等触发层 + jobs/bt_account.py 的正式口径）：")
    print("        · **触发是否真的命中**（253/254 条件当天可能一次都不触发 → 粗估等于「假设都成交」＝**上限**）")
    print("          ⚠️ 实测 2026-01-05~08 的 51 个腿-标的日**全部未触发**（正式账户层 0 成交）→ 粗估显著高估")
    print("        · **成交时刻与价格**（粗估按腿当日收盘，正式按分钟 bar 触发时刻 ×(1+滑点)）")
    print("        · **卖腿/止损/高抛**（粗估只买不卖 → 是「买而不卖」基线）")
    print("        · **T+1**（只约束卖出；只买不卖时无影响，但一旦有卖腿就生效）")
    print("        · 涨停禁卖、t_gateway 其余护栏（日亏损熔断 / 单笔超净值 5% 告警统计等）")
    if blocked:
        print("     · 本次被拦：%s" % blocked)
    if len(per) > 1:
        srt = sorted(per)
        print("   分位：最好 %+.1f%% / 中位 %+.1f%% / 最差 %+.1f%%"
              % (srt[-1] * 100, srt[len(srt) // 2] * 100, srt[0] * 100))
    print("\n   分主题（笔数 / 平均收益）：")
    for k in sorted(by_theme, key=lambda x: -sum(by_theme[x]) / len(by_theme[x])):
        v = by_theme[k]
        print("     %-16s %3d 笔  %+.2f%%" % (k, len(v), sum(v) / len(v) * 100))
    print("\n   分腿型：")
    for k in sorted(by_type):
        v = by_type[k]
        print("     %-16s %3d 笔  %+.2f%%" % (k, len(v), sum(v) / len(v) * 100))
    return 0


def formal_report(account_json: str):
    d = json.load(open(account_json, encoding="utf-8"))
    run = d.get("run") or {}
    s = d.get("summary") or {}
    curve = d.get("curve") or []
    print("== 正式口径：%s 模式（%s → %s）" % (run.get("mode"), (curve[0]["day"] if curve else "-"),
                                              (curve[-1]["day"] if curve else "-")))
    print("   初始 %.0f → 期末 %.2f   区间收益 %+.2f%%   费用 %.2f"
          % (run.get("initial", 0), s.get("final_equity", 0), s.get("return_pct", 0),
             s.get("commission_total", 0)))
    print("   买 %d 笔 / 卖 %d 笔 / 已实现 %.2f / 未平仓 %d"
          % (s.get("n_buy", 0), s.get("n_sell", 0), s.get("realized_total", 0), s.get("positions_open", 0)))
    if curve:
        peak, mdd = curve[0]["equity"], 0.0
        for c in curve:
            peak = max(peak, c["equity"])
            mdd = min(mdd, c["equity"] / peak - 1)
        print("   最大回撤 %.2f%%" % (mdd * 100))
        print("\n   逐日净值（最近 12 个交易日）：")
        for c in curve[-12:]:
            print("     %s  现金 %11.2f  市值 %11.2f  净值 %11.2f  持仓 %d"
                  % (c["day"], c["cash"], c["mv"], c["equity"], c["n_pos"]))
    for key, title in (("by_theme", "分主题"), ("by_leg_type", "分腿型")):
        agg = d.get(key) or {}
        if not agg:
            continue
        print("\n   %s（买入额 / 卖出额 / 笔数 / 费用）：" % title)
        for k, v in sorted(agg.items(), key=lambda kv: -(kv[1].get("buy_amount", 0))):
            print("     %-16s 买 %10.0f  卖 %10.0f  %d/%d 笔  费 %.1f"
                  % (k, v.get("buy_amount", 0), v.get("sell_amount", 0), v.get("n_buy", 0),
                     v.get("n_sell", 0), v.get("fee", 0)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(bt_env.DATA, "_bt_year"))
    ap.add_argument("--bars-db", default=os.path.join(bt_env.DATA, "_bt_full", "bars.sqlite"))
    ap.add_argument("--account", default="")
    ap.add_argument("--rough", action="store_true", help="只出粗估预览（不显示正式口径）")
    ap.add_argument("--no-rough", action="store_true", help="只出正式口径（不附粗估对照）")
    ap.add_argument("--initial", type=float, default=250000.0, help="粗估：初始资金")
    ap.add_argument("--budget", type=float, default=25000.0, help="粗估：单笔预算（生产试仓档 ≈ 2.5 万/笔）")
    ap.add_argument("--entry", default="same_day_close", choices=["same_day_close", "next_open"],
                    help="粗估成交口径：腿当日收盘（默认）/ 次一交易日开盘")
    a = ap.parse_args()

    acc = a.account
    if not acc:
        cands = sorted(glob.glob(os.path.join(a.root, "_summary", "account_*.json")))
        # 优先 hold（与"粗估上限"口径同族，便于对照）
        acc = next((c for c in cands if "hold" in os.path.basename(c)), cands[0] if cands else "")
    if a.rough:
        return rough_report(a.root, a.bars_db, initial=a.initial, budget=a.budget, entry_mode=a.entry)
    if acc and os.path.exists(acc):
        rc = formal_report(acc)
        if not a.no_rough:
            print("\n" + "─" * 72)
            print("（对照）下面是**粗估上限**口径：假设每笔腿当天都成交、一次建满仓")
            rough_report(a.root, a.bars_db, initial=a.initial, budget=a.budget, entry_mode=a.entry)
            _compare_block(a.root, acc)
        return rc
    return rough_report(a.root, a.bars_db, initial=a.initial, budget=a.budget, entry_mode=a.entry)


def _compare_block(root: str, acc_json: str):
    """正式 vs 粗估上限的关键差异：腿数 / 成交数 / 成交率（口径是否"假设都成交"）。"""
    try:
        d = json.load(open(acc_json, encoding="utf-8"))
        s = d.get("summary") or {}
        legs = load_legs(root)
        # ⚠️ 两侧窗口必须对齐：正式账户只覆盖 run.days，粗估腿可能是**全量**（跑批还在推进）
        win = set(d.get("run", {}).get("days") or [])
        if win:
            legs = {k: v for k, v in legs.items() if k in win}
        n_leg = sum(len(v) for v in legs.values())
        n_sym = len({(k, x["symbol"]) for k, v in legs.items() for x in v})
        n_sym = len({(k, x["symbol"]) for k, v in legs.items() for x in v})
        print("\n== 口径对照（关键差异）")
        print("   腿（计划）：%d 笔（%d 个 标的×日）" % (n_leg, n_sym))
        print("   正式成交：%d 笔（成交率 %.0f%% 相对标的×日）   ← 触发条件的稀疏性"
              % (s.get("n_buy", 0), 100.0 * (s.get("n_buy", 0) / max(n_sym, 1))))
        print("   正式收益：%+.2f%%（初始 %.0f → 净值 %.2f）"
              % (s.get("return_pct", 0), d["run"].get("initial", 0), s.get("final_equity", 0)))
        print("   粗估上限：假设上述 %d 个标的×日全部成交（因此必然比正式收益乐观）" % n_sym)
        print("   窗口：两端对齐到 %s → %s（跑批推进后请重跑 `run_formal_incremental.sh` 让正式口径跟上）"
              % (win and min(win) or "-", win and max(win) or "-"))
    except Exception as e:
        print("（对照计算失败：%s）" % str(e)[:80])


if __name__ == "__main__":
    sys.exit(main())
