# -*- coding: utf-8 -*-
"""eval_theme_vol_fund.py — 他称的「**选板块第一要素**」 vs 我们现用的方向层池判据（2026-09-15）。

## 他的话（逐字，xls2025 可核）
> 2025-06-16「**量能活跃**(也就是最近一周内至少2/3天数以上在10日量能以上)，**资金没有5日连续流出**的。
>   这是选板块的第一要素」

## 我们现在的池判据（生产，D1 方向层）
`wolf_mainline_select.run()`：`share5 = 主题近5日成交额 / 全市场近5日成交额` 取 topK(=3) ∩ `r5>0`
（r5 = 主题等权复利 − 全市场等权复利）→ 池。**该口径是我们自造**（参数总账 §5-1）。

## 本脚本做什么（只做对照，不改生产）
对每个交易日：
  · **他的口径** `H` = ①主题近 5 日中「成交额 > 该主题 10 日成交额均值」的天数 ≥ ceil(5×2/3)=4
                    ∧ ②主题近 5 日主力净流入**没有**连续 5 日为负（任一主题成分的 net_mf_amount 加总）
  · **我们的口径** `O` = share5 topK(=3) ∩ r5>0（调用生产同款 `MS.score_day`）
尺子：**主题后 5 日超额** = 主题等权复利 − 全市场等权复利（同 `jobs/eval_mainline_select.py`），
块状 t 按 ISO 周（复用 `jobs/eval_leg_metrics.block_t`）。

用法：`.venv/bin/python jobs/eval_theme_vol_fund.py [--start 20260105] [--end 20260904] [--k 3]`
"""
import argparse
import collections
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import eval_pick_selection as E          # noqa: E402
from eval_leg_metrics import block_t     # noqa: E402

VOL_WIN = 10          # 他："10日量能"
ACT_DAYS = 5          # 他："最近一周"
ACT_NEED = 4          # 他："至少2/3天数以上" → ceil(5×2/3)=4
FUND_DAYS = 5         # 他："资金没有5日连续流出的"
FWD = 5               # 尺子：后 5 日超额


def stat(pairs):
    v = [x for _, x in pairs if x is not None and x == x]
    if not v:
        return {"n": 0}
    a = np.array(v, dtype=float)
    sd = float(a.std(ddof=1)) if len(a) > 1 else 0.0
    bt = block_t([(d, float(x)) for d, x in pairs if x is not None and x == x])
    return {"n": len(a), "mean": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
            "win": round(float((a > 0).mean()), 3),
            "block_t": bt.get("t"), "blocks": bt.get("blocks")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260904")
    ap.add_argument("--k", type=int, default=3, help="我们口径的 topK（生产 WOLF_MS_POOL_K=3）")
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_theme_vol_fund.json"))
    args = ap.parse_args()

    panel = E.Panel()
    MF = E.load_moneyflow(panel, "moneyflow")
    uni, lead, allc, MS = E.load_universe()
    days_all = panel.dates
    days = [d for d in days_all if args.start <= d <= args.end and panel.di[d] >= 260]
    print("[tvf] %d 天 × %d 主题 | 资金流=%s" % (len(days), len(uni), "有" if MF is not None else "无"), flush=True)

    # 主题成交额（亿元）矩阵：T × 主题
    A = pd.DataFrame(panel.amt, index=panel.dates, columns=panel.codes).fillna(0.0)
    theme_amt = {}
    for th, codes in uni.items():
        cc = [c for c in codes if c in A.columns]
        theme_amt[th] = A[cc].sum(axis=1) / 1e5 if cc else pd.Series(0.0, index=panel.dates)
    TA = pd.DataFrame(theme_amt)
    TA10 = TA.rolling(VOL_WIN).mean()                     # 10 日量能（含当日，标准 MA10 读法）
    # 主题主力净流入（万元）：成分加总
    theme_mf = {}
    for th, codes in uni.items():
        idx = [panel.ci[c] for c in codes if c in panel.ci]
        theme_mf[th] = np.nansum(MF[:, idx], axis=1) if (MF is not None and idx) else np.full(panel.T, np.nan)
    TF = pd.DataFrame({k: v for k, v in theme_mf.items()}, index=panel.dates)

    # 生产同款：份额+r5 → 池（用 MS.score_day，与 jobs/wolf_mainline_select.run 一致）
    pctw = pd.DataFrame(panel.pct, index=panel.dates, columns=panel.codes)
    px = {d: {c: float(v) for c, v in pctw.loc[d].dropna().items()} for d in panel.dates}
    mamt5 = TA.sum(axis=1).rolling(5).sum()
    pool_by_day = {}
    for d in days:
        i = panel.di[d]
        share5 = {}
        for th in uni:
            s = TA[th].rolling(5).sum() / mamt5.replace(0, np.nan)
            v = s.iloc[i]
            if v == v:
                share5[th] = float(v)
        res = MS.score_day(panel.dates, i, px, uni, lead, allc, share5=share5, pool_k=args.k)
        pool_by_day[d] = list(res.get("pool") or [])

    # 他的口径
    his_by_day = {}
    for d in days:
        i = panel.di[d]
        keep = []
        for th in uni:
            act = 0
            for k in range(i - ACT_DAYS + 1, i + 1):
                if k >= 0 and TA[th].iloc[k] > TA10[th].iloc[k]:
                    act += 1
            if act < ACT_NEED:
                continue
            seg = TF[th].iloc[i - FUND_DAYS + 1:i + 1].to_numpy(dtype=float)
            seg = seg[~np.isnan(seg)]
            if len(seg) == FUND_DAYS and bool((seg < 0).all()):
                continue                                   # 连续 5 日净流出 → 剔除
            keep.append(th)
        his_by_day[d] = keep

    # 尺子：主题后 5 日超额
    mkt = {d: (float(np.nanmean(panel.pct[panel.di[d]])) / 100.0) for d in panel.dates}
    fwd = {}
    for d in days:
        i = panel.di[d]
        seg = panel.dates[i + 1:i + 1 + FWD]
        if len(seg) < FWD:
            continue
        m = 1.0
        for dd in seg:
            m *= (1 + mkt.get(dd, 0.0))
        for th, codes in uni.items():
            cc = [panel.ci[c] for c in codes if c in panel.ci]
            c = 1.0
            for dd in seg:
                v = panel.pct[panel.di[dd], cc] / 100.0
                v = v[~np.isnan(v)]
                if len(v):
                    c *= (1 + float(v.mean()))
            fwd[(d, th)] = (c - m) * 100.0

    rec = {"window": [days[0], days[-1]], "k": args.k, "vol_win": VOL_WIN, "act_need": f"{ACT_NEED}/{ACT_DAYS}",
           "fund_days": FUND_DAYS, "fwd": FWD, "arms": {}, "overlap": {}, "by_day": []}
    sets = {"H": [], "O": [], "H_only": [], "O_only": [], "H∩O": [], "all": []}
    for d in days:
        h, o = his_by_day.get(d, []), pool_by_day.get(d, [])
        for th in uni:
            v = fwd.get((d, th))
            if v is None:
                continue
            sets["all"].append((d, v))
            if th in h:
                sets["H"].append((d, v))
            if th in o:
                sets["O"].append((d, v))
            if th in h and th in o:
                sets["H∩O"].append((d, v))
            elif th in h:
                sets["H_only"].append((d, v))
            elif th in o:
                sets["O_only"].append((d, v))
        rec["by_day"].append({"d": d, "H": h, "O": o,
                              "hit": len(set(h) & set(o)), "H_n": len(h), "O_n": len(o)})
    for k, v in sets.items():
        rec["arms"][k] = stat(v)
    # 覆盖率 / 重合
    hs = sum(1 for r in rec["by_day"] if r["H"])
    os_ = sum(1 for r in rec["by_day"] if r["O"])
    hit = sum(r["hit"] for r in rec["by_day"])
    rec["overlap"] = {"days_H_nonempty": hs, "days_O_nonempty": os_,
                      "pairs_H": sum(r["H_n"] for r in rec["by_day"]),
                      "pairs_O": sum(r["O_n"] for r in rec["by_day"]),
                      "pairs_inter": hit}
    # 逐对明细（供稳健性拆分：|H| 大小 / 是否池内 / 日期）
    pairs = []
    for d in days:
        h, o = set(his_by_day.get(d, [])), set(pool_by_day.get(d, []))
        for th in uni:
            v = fwd.get((d, th))
            if v is None:
                continue
            pairs.append({"d": d, "th": th, "in_H": th in h, "in_O": th in o,
                          "H_n": len(h), "O_n": len(o), "fwd": round(float(v), 4)})
    rec["pairs"] = pairs
    # 稳健性拆分：池内样本按 |H| 大小分档
    for tag, cond in (("O∩H_strict(|H|<=3)", lambda p: p["in_O"] and p["in_H"] and p["H_n"] <= 3),
                      ("O∩H_loose(|H|>=4)", lambda p: p["in_O"] and p["in_H"] and p["H_n"] >= 4),
                      ("O_notH", lambda p: p["in_O"] and not p["in_H"]),
                      ("H_only_strict(|H|<=3)", lambda p: p["in_H"] and not p["in_O"] and p["H_n"] <= 3)):
        rec["arms"][tag] = stat([(p["d"], p["fwd"]) for p in pairs if cond(p)])
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(rec, fh, ensure_ascii=False, indent=1, default=float)

    print("\n%-10s %6s %8s %8s %8s %8s" % ("集合", "n", "超额均值%", "中位%", "胜率", "块状t"))
    for k in ("all", "H", "O", "H∩O", "H_only", "O_only", "O∩H_strict(|H|<=3)", "O∩H_loose(|H|>=4)",
              "O_notH", "H_only_strict(|H|<=3)"):
        a = rec["arms"][k]
        print("%-10s %6s %8s %8s %8s %8s" % (k, a.get("n"), a.get("mean"), a.get("median"), a.get("win"), a.get("block_t")))
    print("\n覆盖：他的口径 %d 天非空（平均 %.1f 个主题/天）| 我们的池 %d 天非空（平均 %.1f）| 交集 %d 对"
          % (hs, rec["overlap"]["pairs_H"] / max(1, hs), os_, rec["overlap"]["pairs_O"] / max(1, os_), hit))
    print("[tvf] 已写 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
