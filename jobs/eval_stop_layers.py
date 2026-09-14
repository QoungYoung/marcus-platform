# -*- coding: utf-8 -*-
"""eval_stop_layers.py — R8：**止损口径对照**（阶段 3）。

问题（plan §5 阶段 3 / audit §6 R8）：狼大 2026-08-27「**只看指数大级别**如果不走大5浪而转为下跌1浪就止损」
= 他主张**个股不止损**；我们生产是**六层止损**（个股早停 −3% / 13 日逻辑时间 / 破位 / 上影 + BOLL 中轨 + 指数级）。
本脚本用同一批腿做离线对照，回答两件事：
  ① 个股止损到底是在"止血"还是在"割在最低点"（= 被止损后 N 日的反事实收益）；
  ② 只保留指数级止损会不会更好（收益/尾部/回撤）。

样本：默认 428 条 bt_pit 参考触发腿（`--sample prod` 可换生产腿，但只有 n≈33，仅作探索）。

臂（arms）：
  C_hold    不止损，持到 20 个交易日（基准）
  A1_stock  个股早停（建仓前 13 根K的波段低点 ×(1−3%)，收盘确认）+ 13 日逻辑时间止损
            —— 生产 `_check_stop_loss` / `_check_logic_time_stop` 的可算化近似
  A1_touch  同 A1，但止损用**盘中触及**（low ≤ 线）而非收盘确认（敏感性）
  A2_cur    A1 + 指数级止损代理（= 生产"六层"里可算的部分）
  B_idx20   只指数级止损（代理 = 上证收盘 < MA20）
  B_idx20c  只指数级止损（代理 = 连续 2 日收盘 < MA20，防抖）
  B_idx60   只指数级止损（代理 = 上证收盘 < MA60）
  H60_logic 指数(MA60) + 13 日逻辑时间止损（去掉个股 −3% 早停）—— 拆解用
  H60_early 指数(MA60) + 个股 −3% 早停（去掉 13 日逻辑时间）—— 拆解用

⚠️ 指数级"大级别转下跌1浪"**没有连续历史序列**（data/ 里只有 34 个 wave_state 快照），
   故指数臂一律用 MA 破位**代理**并明确标注；不得把代理结论说成"他的原话已验证"。

用法：
  .venv/bin/python jobs/eval_stop_layers.py                 # 428 参考腿
  .venv/bin/python jobs/eval_stop_layers.py --sample prod   # 生产腿（探索）
  .venv/bin/python jobs/eval_stop_layers.py --horizon 10
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import eval_leg_metrics as M  # noqa: E402

EARLY_DAYS = 13          # 建仓初期窗口（狼大「13 日内」）
EARLY_STOP_PCT = 0.03    # 波段低点 −3%
PRIOR_WIN = 13           # 前高/波段低点取建仓前 13 根 K


def _idx_series() -> dict:
    """上证日线 {date8: close}（data/index_daily_000001.json，2025-01→2026-09）。"""
    p = os.path.join(ROOT, "data", "index_daily_000001.json")
    try:
        rows = json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for r in rows:
        d = str(r.get("trade_date") or "").replace("-", "")
        c = M._num(r.get("close"))
        if d and c:
            out[d] = c
    return out


def _ma_flags(idxc: dict, win: int, consec: int = 1) -> dict:
    """{date8: True} = 当日触发指数破位（收盘 < MA(win)，可要求连续 consec 日）。"""
    days = sorted(idxc)
    flags, below_streak = {}, 0
    for i, d in enumerate(days):
        if i + 1 < win:
            below_streak = 0
            continue
        ma = sum(idxc[x] for x in days[i + 1 - win:i + 1]) / win
        if idxc[d] < ma:
            below_streak += 1
        else:
            below_streak = 0
        if below_streak >= consec:
            flags[d] = True
    return flags


def simulate(rows, i, entry, idx_flags, idx_days, horizon, arm,
             early_pct=EARLY_STOP_PCT, touch=False, post_win=10):
    """返回 (收益%, 出场方式, 持有天数, MAE%, 出场后 10 日最大反弹%, 出场后 10 日最深跌幅%)。

    rows=日线元组序列, i=入场下标；后两项用于回答"是不是割在最低点"。
    """
    prior = rows[max(0, i - PRIOR_WIN):i]
    swing_low = min([r[3] for r in prior if r[3] > 0], default=None)
    prior_high = max([r[2] for r in prior if r[2] > 0], default=None)
    stop_line = swing_low * (1 - early_pct) if swing_low else None
    touched_high = False
    worst = 0.0
    n = min(horizon, len(rows) - i - 1)
    for k in range(1, n + 1):
        r = rows[i + k]
        d8, high, low, close = str(r[0]), r[2], r[3], r[4]
        if low > 0:
            worst = min(worst, (low / entry - 1) * 100)
        if prior_high and high >= prior_high:
            touched_high = True
        # ① 个股早停（只在建仓初期 13 个交易日内）
        use_early = arm in ("A1_stock", "A1_touch", "A2_cur", "H60_early")
        use_logic = arm in ("A1_stock", "A1_touch", "A2_cur", "H60_logic")
        if use_early and k <= EARLY_DAYS and stop_line:
            hit = (low <= stop_line) if (touch or arm == "A1_touch") else (close <= stop_line)
            if hit:
                return ((close / entry - 1) * 100, "early_stop", k, worst) + _post(rows, i + k, close, post_win)
        # ② 13 日逻辑时间止损（窗口走完仍未碰前高）
        if use_logic and k == EARLY_DAYS and prior_high and not touched_high:
            return ((close / entry - 1) * 100, "logic_time", k, worst) + _post(rows, i + k, close, post_win)
        # ③ 指数级止损
        if arm in ("A2_cur", "B_idx20", "B_idx20c", "B_idx60", "H60_logic", "H60_early") and d8 in idx_flags:
            return ((close / entry - 1) * 100, "index_stop", k, worst) + _post(rows, i + k, close, post_win)
    j = i + max(1, n)
    return ((rows[j][4] / entry - 1) * 100, "hold_end", max(1, n), worst) + _post(rows, j, rows[j][4], post_win)


def _post(rows, j, exit_px, win=10):
    """出场后 win 日的 (最大反弹%, 最深跌幅%)，相对于出场价。"""
    seg = rows[j + 1:j + 1 + win]
    if not seg or exit_px <= 0:
        return (None, None)
    hi = max(r[2] for r in seg)
    lo = min(r[3] for r in seg if r[3] > 0)
    return (round((hi / exit_px - 1) * 100, 2), round((lo / exit_px - 1) * 100, 2))


def bucket(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return {}
    v.sort()
    m = st.mean(v)
    sd = st.pstdev(v) if len(v) > 1 else 0.0
    p = lambda q: v[min(len(v) - 1, max(0, int(q * (len(v) - 1))))]  # noqa: E731
    return {"n": len(v), "mean": round(m, 2), "median": round(st.median(v), 2),
            "win": round(sum(1 for x in v if x > 0) / len(v), 4),
            "t": round(m / (sd / len(v) ** 0.5), 2) if sd else None,
            "p10": round(p(0.10), 2), "p5": round(p(0.05), 2),
            "worst5": round(st.mean(v[:5]), 2)}


ARMS = ("C_hold", "A1_stock", "A1_touch", "A2_cur", "B_idx20", "B_idx20c", "B_idx60",
        "H60_logic", "H60_early")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", default="replay", choices=("replay", "prod"))
    ap.add_argument("--horizon", type=int, default=20)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    out_p = args.out or os.path.join(ROOT, ".dsh-tmp", "wolfbt", "legmetrics",
                                     "eval_stop_layers_%s.json" % args.sample)

    idxc = _idx_series()
    flags = {"B_idx20": _ma_flags(idxc, 20), "B_idx20c": _ma_flags(idxc, 20, consec=2),
             "B_idx60": _ma_flags(idxc, 60)}
    flags["A2_cur"] = flags["B_idx20"]
    flags["C_hold"] = {}
    flags["A1_stock"] = {}
    flags["A1_touch"] = {}
    flags["H60_logic"] = flags["B_idx60"]
    flags["H60_early"] = flags["B_idx60"]

    if args.sample == "replay":
        legs = M.load_replay_legs()
        bars = M.ReplayBars()
        key_e, key_d, key_p = "ts_code", "arm_date", "entry_px"
        label = "428 参考触发腿（bt_pit）"
    else:
        rows_p, _meta = M.fetch_paper_trades()
        all_legs, _o, _a, _diag = M.build_legs(rows_p)
        legs = [l for l in all_legs if l["account"] in ("stock", "t") and l["entry_px"]
                and l["realized_pct"] is not None and abs(M._num(l.get("db_profit"))) > 1e-9]
        bars = M.Bars()
        key_e, key_d, key_p = "symbol", "entry_date", "entry_px"
        label = "生产腿（stock+t）"

    res = {a: [] for a in ARMS}
    detail = []
    for l in legs:
        sym, d0, px = l[key_e], str(l[key_d]), float(l[key_p] or 0)
        rows = bars.get(M.norm_symbol(sym))
        i = M.snap_idx(bars, M.norm_symbol(sym), d0)
        if i is None or px <= 0:
            continue
        rec = {"symbol": sym, "date": d0, "entry": px}
        for a in ARMS:
            v, how, k, worst, rb10, dd10 = simulate(rows, i, px, flags[a], idxc, args.horizon, a)
            res[a].append((d0, v))
            rec[a] = round(v, 3)
            rec[a + "_how"] = how
            rec[a + "_days"] = k
            rec[a + "_mae"] = round(worst, 2)
            rec[a + "_reb10"] = rb10
            rec[a + "_dd10"] = dd10
        detail.append(rec)

    print("样本 = %s，n=%d，horizon=%d 交易日；指数代理来自 data/index_daily_000001.json（%d 天）"
          % (label, len(detail), args.horizon, len(idxc)))
    print("\n%-10s %4s %7s %7s %7s %7s %8s %8s %8s  出场结构" % (
        "臂", "n", "均值%", "中位%", "胜率", "t", "p10", "最差5", "p5"))
    out = {"_meta": {"sample": args.sample, "n": len(detail), "horizon": args.horizon},
           "arms": {}}
    for a in ARMS:
        vals = [v for _, v in res[a]]
        b = bucket(vals)
        hows = {}
        for r in detail:
            hows[r[a + "_how"]] = hows.get(r[a + "_how"], 0) + 1
        b["how"] = hows
        b["block_t"] = M.block_t(res[a])
        out["arms"][a] = b
        print("%-10s %4d %7.2f %7.2f %7.3f %7.2f %8.2f %8.2f %8.2f  %s" % (
            a, b["n"], b["mean"], b["median"], b["win"], b["t"] or 0, b["p10"], b["worst5"], b["p5"], hows))

    # 被个股止损的腿：反事实（若不止损持到 horizon）
    print("\n【止损质量：被 A1_stock 止损的腿，反事实 = 不止损持到末端】")
    for a in ("A1_stock", "A2_cur", "B_idx20", "B_idx60", "H60_logic", "H60_early"):
        cf = [(r[a], r["C_hold"]) for r in detail if r[a + "_how"] in ("early_stop", "logic_time", "index_stop")]
        if not cf:
            continue
        real = [x for x, _ in cf]
        hold = [y for _, y in cf]
        better = sum(1 for x, y in cf if y < x)
        reb = [r[a + "_reb10"] for r in detail if r[a + "_how"] in ("early_stop", "logic_time", "index_stop")
               and r[a + "_reb10"] is not None]
        dd = [r[a + "_dd10"] for r in detail if r[a + "_how"] in ("early_stop", "logic_time", "index_stop")
              and r[a + "_dd10"] is not None]
        out["stop_quality_%s" % a] = {"n": len(cf), "realized_mean": round(st.mean(real), 2),
                                      "realized_median": round(st.median(real), 2),
                                      "counterfactual_mean": round(st.mean(hold), 2),
                                      "counterfactual_median": round(st.median(hold), 2),
                                      "stop_was_right_share": round(better / len(cf), 4),
                                      "post_rebound10_mean": round(st.mean(reb), 2) if reb else None,
                                      "post_drawdown10_mean": round(st.mean(dd), 2) if dd else None}
        print("  %-9s n=%-4d 实际(均/中位) %+6.2f/%+6.2f%% | 反事实(均/中位) %+6.2f/%+6.2f%% | 止损正确率 %.0f%%"
              " | 出场后10日 反弹均值 %+5.2f%% 续跌均值 %+5.2f%%"
              % (a, len(cf), st.mean(real), st.median(real), st.mean(hold), st.median(hold),
                 100 * better / len(cf), st.mean(reb) if reb else 0, st.mean(dd) if dd else 0))

    # H1/H2
    ds = sorted(r["date"] for r in detail)
    mid = ds[len(ds) // 2] if ds else "99999999"
    print("\n【H1/H2（中位 %s）】" % mid)
    for a in ("C_hold", "A1_stock", "A2_cur", "B_idx60", "H60_logic", "H60_early"):
        h1 = [v for d, v in res[a] if d < mid]
        h2 = [v for d, v in res[a] if d >= mid]
        b1, b2 = bucket(h1), bucket(h2)
        out.setdefault("halves", {})[a] = {"H1": b1, "H2": b2}
        print("  %-9s H1 n=%-4s 均值 %+6.2f 胜率 %.2f | H2 n=%-4s 均值 %+6.2f 胜率 %.2f"
              % (a, b1.get("n"), b1.get("mean", 0), b1.get("win", 0),
                 b2.get("n"), b2.get("mean", 0), b2.get("win", 0)))

    os.makedirs(os.path.dirname(out_p), exist_ok=True)
    json.dump({"summary": out, "legs": detail}, open(out_p, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n→ %s" % out_p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
