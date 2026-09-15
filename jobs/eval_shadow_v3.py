# -*- coding: utf-8 -*-
"""eval_shadow_v3.py — 影子期对照：把灰度期累积的**影子记录**变成证据（2026-09-15 round 7）。

只读影子产物，**不改任何决策**。灰度期每天会自动写：

* `data/rank_v3_<as_of>.json`            —— v3 条件化排序**会选什么** vs 现行 leader **实际选什么**（同日同主题）
* `data/theme_volfund_shadow_<date>.json` —— 他的"选板块第一要素"（P1）**会拦掉哪些主题**

本脚本用**与阶段 0 / `eval_pick_rank_v3.py` 同一把尺子**给这些记录算前瞻收益：
入场 = 影子日**次日收盘**，出场 = 入场后 `hold` 个交易日收盘，超额 = 该票收益 − **同主题等权篮子**收益，
显著性用**按 ISO 周分块的块状 t**（`eval_leg_metrics.block_t`）。

回答两件事：
① **v3 的影子选票是否真的优于现行 leader**（同日同主题配对差）——这是 `WOLF_PICK_RANK_V3=1` 拍板所需证据；
② **被 P1 拦掉的主题，其篮子后续是不是真的差** ——这是 `WOLF_THEME_VOLFUND_GATE=1` 拍板所需证据。

纪律：**n < 100 只作探索性**；前瞻窗口没走完的 theme-day 记 `pending` 并排除（不当作 0）。
样本随影子天数自动累积，攒够再下结论。

用法::

    .venv/bin/python jobs/eval_shadow_v3.py                    # 读 ./data 下的影子文件
    .venv/bin/python jobs/eval_shadow_v3.py --shadow-dir /app/data --hold 10
    .venv/bin/python jobs/eval_shadow_v3.py --bars <prod 拉下来的 bars.parquet>

说明：`uni`（主题成分）走 `eval_pick_selection.load_universe()`，其 DB 路径由
`WOLF_MS_UNIVERSE_DB` 覆盖（生产上指 `/app/data/stock_pool.db`）。
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "backend"), os.path.join(ROOT, "apps", "main_line"),
           os.path.join(ROOT, "jobs")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import eval_pick_selection as E  # noqa: E402

DEFAULT_SHADOW_DIR = os.path.join(ROOT, "data")
DEFAULT_OUT = os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_shadow_v3.json")
MIN_N = 100          # 低于此样本量只作探索性（用户红线：n<100 exploratory）


def xq_to_ts(x):
    """`'SH603986'` → `'603986.SH'`；已是 ts_code（`'603986.SH'`）则原样返回；无法识别返回 None。"""
    x = str(x or "").strip().upper()
    if not x:
        return None
    if "." in x:
        return x
    if len(x) >= 8 and x[:2] in ("SH", "SZ", "BJ"):
        return "%s.%s" % (x[2:], x[:2])
    return None


def load_rank_shadow(shadow_dir):
    """读 `rank_v3_*.json` → [{date, theme, mode, qtile, domain_n, v3:[xq], leader:[xq]}]。"""
    out = []
    for p in sorted(glob.glob(os.path.join(shadow_dir, "rank_v3_*.json"))):
        d = os.path.basename(p)[len("rank_v3_"):-len(".json")]
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception as e:                                  # pragma: no cover
            print("[shadow] 读取失败 %s: %s" % (p, str(e)[:80]), file=sys.stderr)
            continue
        for th, v in (j.get("themes") or {}).items():
            out.append({"date": str(v.get("date") or j.get("date") or d)[:8] or d,
                        "theme": th, "mode": v.get("mode"), "qtile": v.get("theme_r5_qtile"),
                        "domain_n": v.get("domain_n"),
                        "v3": [x.get("symbol") for x in (v.get("v3") or [])],
                        "leader": [x.get("symbol") for x in (v.get("leader") or [])]})
    return out


def load_p1_shadow(shadow_dir):
    """读 `theme_volfund_shadow_*.json` → [{date, theme, passed, why}]（passed=False = 他这一条会拦掉该主题）。"""
    out = []
    for p in sorted(glob.glob(os.path.join(shadow_dir, "theme_volfund_shadow_*.json"))):
        d = os.path.basename(p)[len("theme_volfund_shadow_"):-len(".json")]
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception as e:                                  # pragma: no cover
            print("[shadow] 读取失败 %s: %s" % (p, str(e)[:80]), file=sys.stderr)
            continue
        for th, v in (j.get("themes") or {}).items():
            out.append({"date": str(j.get("date") or d)[:8] or d, "theme": th,
                        "passed": bool(v.get("pass")), "why": v.get("why")})
    return out


def _fwd(panel, i, xq, hold):
    """影子日 i 的某票前瞻收益（%）。返回 (值, 原因)；原因非 None 即为不可评估。"""
    ts = xq_to_ts(xq)
    if ts is None:
        return None, "bad_symbol"
    j = panel.ci.get(ts)
    if j is None:
        return None, "no_bars"
    r = panel.fwd_ret(i, hold, 1)
    if r is None:
        return None, "pending"
    v = float(r[j])
    if v != v:
        return None, "nan"
    return v, None


def _basket(panel, i, theme, uni, hold):
    """同主题等权篮子前瞻收益（%）—— 与 `eval_pick_rank_v3.py` 同口径（主题成分取 load_universe）。"""
    codes = [c for c in (uni.get(theme) or []) if c in panel.ci]
    if not codes:
        return None
    r = panel.fwd_ret(i, hold, 1)
    if r is None:
        return None
    with np.errstate(invalid="ignore"):
        m = np.nanmean(r[[panel.ci[c] for c in codes]])
    return float(m) if m == m else None


def load_gate_blocked(shadow_dir):
    """读 `pick_path_*.json` → [{date, theme, why, allowed}]。

    `gate_blocked` = 当日被**主题门**（A2 结构门 ∧ 资金门）挡掉的主题；`allowed` = 当日实际布了腿的主题
    （从 `legs` 去重）。用来量 A2 那个悬而未决的问题：**门是不是太严**（09-11/09-14/09-15 池内主题全被挡，
    路径 B 根本不进）→ 被挡主题的篮子后续是不是真的更差。
    """
    out = []
    for p in sorted(glob.glob(os.path.join(shadow_dir, "pick_path_*.json"))):
        d = os.path.basename(p)[len("pick_path_"):-len(".json")]
        try:
            j = json.load(open(p, encoding="utf-8"))
        except Exception as e:                                  # pragma: no cover
            print("[shadow] 读取失败 %s: %s" % (p, str(e)[:80]), file=sys.stderr)
            continue
        date = str(j.get("date") or d)[:8] or d
        for b in (j.get("gate_blocked") or []):
            out.append({"date": date, "theme": b.get("theme"), "why": b.get("why"), "allowed": False})
        for th in sorted({str(x.get("theme") or x.get("chain") or "")
                          for x in (j.get("legs") or [])} - {""}):
            out.append({"date": date, "theme": th, "why": "当日有布腿", "allowed": True})
    return out


def evaluate(rank_rows, p1_rows, panel, uni, hold=5, gate_rows=None):
    """核心：把影子记录算成逐 theme-day 明细 + 聚合统计（纯函数，便于单测）。"""
    detail, reason_cnt = [], {}
    for row in rank_rows:
        i = panel.di.get(str(row["date"]))
        if i is None:
            reason_cnt["date_not_in_bars"] = reason_cnt.get("date_not_in_bars", 0) + 1
            continue
        bk = _basket(panel, i, row["theme"], uni, hold)
        rec = {"date": row["date"], "theme": row["theme"], "qtile": row.get("qtile"),
               "domain_n": row.get("domain_n"), "mode": row.get("mode"), "basket": bk,
               "v3": None, "leader": None, "v3_sym": None, "leader_sym": None,
               "v3_ex": None, "leader_ex": None, "reason": None}
        for arm, key in (("v3", "v3"), ("leader", "leader")):
            syms = [s for s in (row.get(key) or []) if s]
            if not syms:
                reason_cnt[key + "_empty"] = reason_cnt.get(key + "_empty", 0) + 1
                continue
            val, why = _fwd(panel, i, syms[0], hold)
            if why:
                reason_cnt[arm + ":" + why] = reason_cnt.get(arm + ":" + why, 0) + 1
                rec["reason"] = why
                continue
            rec[arm + "_sym" if arm == "v3" else "leader_sym"] = syms[0]
            rec[arm] = round(val, 3)
            if bk is not None:
                rec[arm + "_ex"] = round(val - bk, 3)
        detail.append(rec)

    v3v = [r["v3"] for r in detail if r.get("v3") is not None]
    ldv = [r["leader"] for r in detail if r.get("leader") is not None]
    v3ex = [r["v3_ex"] for r in detail if r.get("v3_ex") is not None]
    ldex = [r["leader_ex"] for r in detail if r.get("leader_ex") is not None]
    pairs = [(r["date"], r["v3"] - r["leader"]) for r in detail
             if r.get("v3") is not None and r.get("leader") is not None]
    ex_pairs = [(r["date"], r["v3_ex"] - r["leader_ex"]) for r in detail
                if r.get("v3_ex") is not None and r.get("leader_ex") is not None]

    agg = {"n_v3": len(v3v), "n_leader": len(ldv), "n_paired": len(pairs),
           "v3": E._stat([(r["date"], r["v3"]) for r in detail if r.get("v3") is not None], v3v),
           "leader": E._stat([(r["date"], r["leader"]) for r in detail if r.get("leader") is not None], ldv),
           "v3_ex": E._stat([(r["date"], r["v3_ex"]) for r in detail if r.get("v3_ex") is not None], v3ex),
           "leader_ex": E._stat([(r["date"], r["leader_ex"]) for r in detail if r.get("leader_ex") is not None], ldex),
           "paired": E._stat(pairs, [v for _, v in pairs]),
           "paired_ex": E._stat(ex_pairs, [v for _, v in ex_pairs])}

    p1_detail = []
    for row in p1_rows:
        i = panel.di.get(str(row["date"]))
        if i is None:
            reason_cnt["p1:date_not_in_bars"] = reason_cnt.get("p1:date_not_in_bars", 0) + 1
            continue
        bk = _basket(panel, i, row["theme"], uni, hold)
        p1_detail.append({"date": row["date"], "theme": row["theme"], "passed": row["passed"],
                          "why": row.get("why"), "basket": None if bk is None else round(bk, 3)})
    blocked = [r for r in p1_detail if r["passed"] is False and r["basket"] is not None]
    p1_agg = {"n_checked": len(p1_detail), "n_blocked": len(blocked),
              "blocked_basket": E._stat([(r["date"], r["basket"]) for r in blocked],
                                        [r["basket"] for r in blocked]),
              "passed_basket": E._stat([(r["date"], r["basket"]) for r in p1_detail
                                        if r["passed"] is True and r["basket"] is not None],
                                       [r["basket"] for r in p1_detail
                                        if r["passed"] is True and r["basket"] is not None])}
    # ③ A2 主题门：被挡的主题篮子 vs 当日放行（布了腿）的主题篮子
    gate_rows = gate_rows or []
    g_detail = []
    for row in gate_rows:
        i = panel.di.get(str(row["date"]))
        if i is None:
            reason_cnt["gate:date_not_in_bars"] = reason_cnt.get("gate:date_not_in_bars", 0) + 1
            continue
        bk = _basket(panel, i, row["theme"], uni, hold)
        g_detail.append({"date": row["date"], "theme": row["theme"], "allowed": bool(row.get("allowed")),
                         "why": (row.get("why") or "")[:80], "basket": None if bk is None else round(bk, 3)})
    g_blocked = [r for r in g_detail if not r["allowed"] and r["basket"] is not None]
    g_allowed = [r for r in g_detail if r["allowed"] and r["basket"] is not None]
    gate_agg = {"n_checked": len(g_detail), "n_blocked": len(g_blocked), "n_allowed": len(g_allowed),
                "n_pending": len(g_detail) - len(g_blocked) - len(g_allowed),
                "blocked_basket": E._stat([(r["date"], r["basket"]) for r in g_blocked],
                                          [r["basket"] for r in g_blocked]),
                "allowed_basket": E._stat([(r["date"], r["basket"]) for r in g_allowed],
                                          [r["basket"] for r in g_allowed])}

    return {"hold": hold, "detail": detail, "p1_detail": p1_detail, "gate_detail": g_detail,
            "agg": agg, "p1_agg": p1_agg, "gate_agg": gate_agg, "skipped": reason_cnt}


def _fmt(s):
    if not s or not s.get("n"):
        return "n=0"
    return ("n=%d 均值%+.3f%% 中位%+.3f%% 胜率%.0f%% 块状t=%s(%s块)"
            % (s["n"], s["mean"], s["median"], 100 * s["win"], s.get("block_t"), s.get("blocks")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow-dir", default=DEFAULT_SHADOW_DIR, help="影子产物目录（默认 ./data）")
    ap.add_argument("--bars", default=E.BARS, help="bars parquet（默认本地 .dsh-tmp/buyside/bars.parquet）")
    ap.add_argument("--hold", type=int, default=5, help="持有交易日（默认 5）")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    rank_rows = load_rank_shadow(args.shadow_dir)
    p1_rows = load_p1_shadow(args.shadow_dir)
    gate_rows = load_gate_blocked(args.shadow_dir)
    print("[shadow] rank_v3 theme-day 记录 %d 条 | P1 记录 %d 条 | 主题门(pick_path) 记录 %d 条 | 目录 %s"
          % (len(rank_rows), len(p1_rows), len(gate_rows), args.shadow_dir))
    if not rank_rows and not p1_rows and not gate_rows:
        print("[shadow] 暂无影子记录（灰度期尚未产生，或目录不对）")
        return 0

    panel = E.Panel(args.bars)
    uni, _lead, _allc, _ms = E.load_universe()
    res = evaluate(rank_rows, p1_rows, panel, uni, hold=args.hold, gate_rows=gate_rows)

    bars_max = panel.dates[-1]
    print("\n影子期对照（hold=%d，入场=影子日次日收盘，超额=同主题等权篮子；bars 至 %s）" % (args.hold, bars_max))
    print("可评估 theme-day：v3 %d / leader %d / 配对 %d；跳过原因 %s"
          % (res["agg"]["n_v3"], res["agg"]["n_leader"], res["agg"]["n_paired"],
             res["skipped"] or {}))

    print("\n① v3 影子选票 vs 现行 leader（同日同主题）")
    print("   v3      " + _fmt(res["agg"]["v3"]))
    print("   leader  " + _fmt(res["agg"]["leader"]))
    print("   配对差  " + _fmt(res["agg"]["paired"]))
    print("   超额    v3 " + _fmt(res["agg"]["v3_ex"]) + " | leader " + _fmt(res["agg"]["leader_ex"]))
    print("   超额配对差 " + _fmt(res["agg"]["paired_ex"]))
    if res["agg"]["n_paired"] < MIN_N:
        print("   ⚠️ 配对样本 %d < %d → **探索性**，不足以下结论（继续累积影子天）" % (res["agg"]["n_paired"], MIN_N))

    print("\n② 他的 P1「选板块第一要素」会拦掉的主题（拦对=篮子后续确实差）")
    pa = res["p1_agg"]
    print("   检查 %d 个 theme-day，其中会拦 %d 个" % (pa["n_checked"], pa["n_blocked"]))
    print("   被拦主题篮子 " + _fmt(pa["blocked_basket"]))
    print("   放行主题篮子 " + _fmt(pa["passed_basket"]))
    if pa["n_blocked"] < MIN_N:
        print("   ⚠️ 被拦样本 %d < %d → **探索性**" % (pa["n_blocked"], MIN_N))

    print("\n③ A2 主题门：被挡的主题 vs 当日放行的主题（拦对 = 被挡篮子后续确实差）")
    ga = res["gate_agg"]
    print("   记录 %d 个 theme-day：被挡 %d / 放行 %d / 窗口未走完 %d"
          % (ga["n_checked"], ga["n_blocked"], ga["n_allowed"], ga.get("n_pending", 0)))
    print("   被挡主题篮子 " + _fmt(ga["blocked_basket"]))
    print("   放行主题篮子 " + _fmt(ga["allowed_basket"]))
    if ga["n_blocked"] < MIN_N:
        print("   ⚠️ 被挡样本 %d < %d → **探索性**（每天自动累积，攒够再下结论）" % (ga["n_blocked"], MIN_N))

    print("\n④ 逐 theme-day 明细")
    for r in res["detail"]:
        print("   %s %-12s q=%-4s dom=%-3s v3=%-5s %-7s leader=%-5s %-7s 篮子=%s"
              % (r["date"], r["theme"], r["qtile"], r["domain_n"], r["v3_sym"] or "—",
                 ("%+.2f%%" % r["v3"]) if r.get("v3") is not None else (r.get("reason") or "无"),
                 r["leader_sym"] or "—",
                 ("%+.2f%%" % r["leader"]) if r.get("leader") is not None else "无",
                 ("%+.2f%%" % r["basket"]) if r.get("basket") is not None else "—"))
    for r in res["p1_detail"]:
        print("   %s %-12s P1=%s 篮子=%s  %s"
              % (r["date"], r["theme"], "拦" if r["passed"] is False else "放",
                 ("%+.2f%%" % r["basket"]) if r["basket"] is not None else "—", (r.get("why") or "")[:40]))
    for r in res["gate_detail"]:
        print("   %s %-12s 主题门=%s 篮子=%s  %s"
              % (r["date"], r["theme"], "放" if r["allowed"] else "挡",
                 ("%+.2f%%" % r["basket"]) if r["basket"] is not None else "—", (r.get("why") or "")[:50]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump({"shadow_dir": args.shadow_dir, "bars": args.bars, "bars_max": bars_max,
               "n_rank_rows": len(rank_rows), "n_p1_rows": len(p1_rows),
               "n_gate_rows": len(gate_rows), **res},
              open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[shadow] 写出 %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
