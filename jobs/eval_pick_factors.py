# -*- coding: utf-8 -*-
"""eval_pick_factors.py — ① 选股因子体检：**哪一个"辨识度"代理真的与未来超额正相关？**

背景（`docs/wolf-pick-selection-eval.md`）：现行 leader = r60 + 成交额 + 涨停次数 的**分位均值**，
实测与未来同主题超额**负相关**（IC −0.058~−0.087）。本轮要把"辨识度最高的老龙头"换一个代理，
就必须先把**候选代理逐个量一遍**——语料给假设，数据给结论（用户红线）。

尺子与 `jobs/eval_pick_selection.py` 完全一致（同一 Panel、同一天、同一超额定义、同一块状 t），
只是把"选票"换成"排序"：对每个 主题×日 算 Spearman(因子, 未来同主题超额) 与十分位。

用法：
  .venv/bin/python jobs/eval_pick_factors.py [--start 20260105] [--end 20260904] [--hold 5]
"""
import argparse
import collections
import json
import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import eval_pick_selection as E  # noqa: E402


STOP_TOKENS = {"概念", "板块", "产业", "指数", "设备", "材料", "龙头", "中国", "股份", "集团", "科技",
               "行业", "主题", "相关", "服务", "应用", "领域", "制造", "技术", "其他", "综合", "开发",
               "国家", "国际", "公司", "上市", "投资", "工程", "系统", "产品", "业务", "方案"}


def theme_name_tokens(panel, th_cons, max_df=0.05):
    """每个主题的「名字关键词」：概念名切 2–4 字 token，剔除通用词与**全市场高频** token。

    依据（2026-01-29）：「根据量化做有色金属的打法，**名字优先**」「你觉得是有矿的资金涨得多
    还是**名字是黄金白银的**涨得多?」→ 他的"辨识度"里有一条 = **简称是否含题材核心词**。
    本函数是**我们的代理**（自动切词 + 文档频率过滤，避免"电子/科技"这类通用词误命中）。
    """
    names = {c: str(panel.names.get(c, "")) for c in panel.codes}
    toks = {}
    for th, cons in (th_cons or {}).items():
        t = set()
        for cn in cons:
            base = cn.split("(")[0].split("（")[0]
            for L in (2, 3, 4):
                for k in range(max(0, len(base) - L + 1)):
                    x = base[k:k + L]
                    if len(x) == L and x not in STOP_TOKENS:
                        t.add(x)
        toks[th] = t
    allt = set().union(*toks.values()) if toks else set()
    df = {x: sum(1 for nm in names.values() if x in nm) / max(1, len(names)) for x in allt}
    return {th: sorted(x for x in t if df.get(x, 1.0) <= max_df) for th, t in toks.items()}


def rank_pct(v):
    """分位（0-1，并列取平均名次），与生产 pick_v2.pct_rank 同口径。"""
    return E._nan_pct_rank(np.asarray(v, dtype=float))


def factor_matrix(panel, i, cols, name_toks=None):
    """每个主题日的候选因子（全部只用 ≤ i 的数据；PIT）。"""
    C, A, P, L, H = panel.close, panel.amt, panel.pct, panel.low, panel.high
    c = C[i, cols]
    n = len(cols)
    out = {}
    with np.errstate(invalid="ignore", divide="ignore"):
        out["r60"] = (c / C[i - 60, cols] - 1.0) * 100.0
        out["r20"] = (c / C[i - 20, cols] - 1.0) * 100.0
        out["r5"] = (c / C[i - 5, cols] - 1.0) * 100.0
        out["rs20"] = out["r20"] - np.nanmean(out["r20"])
        out["amt20"] = np.nanmean(A[i - 19:i + 1][:, cols], axis=0) / 1e5          # 亿元
        out["hist"] = np.sum(~np.isnan(C[max(0, i - 249):i + 1][:, cols]), axis=0)  # 有效历史根数
        out["lim60"] = np.nansum(P[i - 59:i + 1][:, cols] >= 9.7, axis=0)
        # 「由缩转放」日线代理：今日成交额 / 前 5 日均额
        base5 = np.nanmean(A[i - 5:i][:, cols], axis=0)
        out["vol_ratio5"] = A[i, cols] / np.where(base5 > 0, base5, np.nan)
        # 距前一日低（低吸可达性）
        out["dist_prevlow"] = (c / L[i - 1, cols] - 1.0) * 100.0
        # 「曾领涨」：过去 60 日中，该股是**主题内当日涨幅第一**的次数（+ 前 20 日次数）
        seg = P[i - 59:i + 1][:, cols]
        with np.errstate(invalid="ignore"):
            am = np.nanargmax(np.where(np.isnan(seg), -np.inf, seg), axis=1)
        cnt = np.zeros(n)
        for k in am:
            if 0 <= k < n:
                cnt[k] += 1
        out["lead_days60"] = cnt
        seg20 = P[i - 19:i + 1][:, cols]
        am20 = np.nanargmax(np.where(np.isnan(seg20), -np.inf, seg20), axis=1)
        cnt20 = np.zeros(n)
        for k in am20:
            if 0 <= k < n:
                cnt20[k] += 1
        out["lead_days20"] = cnt20
        # 主题内当日涨幅排名（负号：越大越强）
        out["pct_today"] = P[i, cols]
        # D2 量能活跃（2025-06-16「最近一周内至少2/3天数以上在10日量能以上」）→ 近 5 日达标天数
        cnt_act = np.zeros(n)
        for k in range(max(1, i - 4), i + 1):
            base = np.nanmean(A[k - 10:k][:, cols], axis=0)
            with np.errstate(invalid="ignore"):
                cnt_act += (A[k, cols] > base).astype(float)
        out["vol_active5"] = cnt_act
        # D6 低位横盘多时（2026-04-07「低位横盘多时的就是好 超跌都没有低位走平多时的好」）：
        #   近 20 日中「日振幅<2% ∧ 收盘位于 20 日区间下半」的天数
        seg_c = C[i - 19:i + 1][:, cols]
        seg_h = H[i - 19:i + 1][:, cols]
        seg_l = L[i - 19:i + 1][:, cols]
        with np.errstate(invalid="ignore"):
            amp = (seg_h - seg_l) / np.where(seg_c > 0, seg_c, np.nan)
            rng_hi = np.nanmax(seg_c, axis=0)
            rng_lo = np.nanmin(seg_c, axis=0)
            half = (seg_c - rng_lo) <= 0.5 * (rng_hi - rng_lo)
            out["flat_low_days"] = np.nansum((amp < 0.02) & half, axis=0)
        # D1 名字辨识度：简称是否含该主题的核心词
        if name_toks:
            subs = [panel.codes[k] for k in cols]
            out["name_hit"] = np.array([1.0 if any(t in panel.names.get(s, "") for t in name_toks)
                                        else 0.0 for s in subs])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260904")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_factors.json"))
    args = ap.parse_args()
    E.HOLD = int(args.hold)

    panel = E.Panel()
    uni, lead, allc, MS = E.load_universe()
    th_cons, cmap = E.theme_concept_sets()
    name_toks_by_theme = theme_name_tokens(panel, th_cons)
    print("[fac] 名字关键词示例: %s" % {k: v[:6] for k, v in list(name_toks_by_theme.items())[:3]}, flush=True)
    days = [d for d in panel.dates if args.start <= d <= args.end and panel.di[d] >= 260]
    print("[fac] %d 天 × %d 主题（hold=%d）" % (len(days), len(uni), args.hold), flush=True)

    ic = collections.defaultdict(list)          # factor -> [(date, ic)]
    dec = collections.defaultdict(lambda: collections.defaultdict(list))
    pools = collections.defaultdict(list)       # 池内（cand）IC
    for d in days:
        i = panel.di[d]
        r_fwd = panel.fwd_ret(i, args.hold, 1)
        if r_fwd is None:
            continue
        for th, codes in uni.items():
            pk = None
            try:
                pk = E.pick_day(panel, i, th, uni, th_cons, cmap)
            except Exception as e:
                print("[fac] pick_day err", d, th, str(e)[:60], flush=True)
            if pk is None or pk["n"] < 10:
                continue
            cols = pk["cols"]
            basket = np.nanmean(r_fwd[[panel.ci[c] for c in codes if c in panel.ci]])
            ex = r_fwd[cols] - basket
            ok = ~np.isnan(ex)
            if ok.sum() < 10:
                continue
            F = factor_matrix(panel, i, cols, name_toks_by_theme.get(th))
            for name, v in F.items():
                v = np.asarray(v, dtype=float)
                m = ok & ~np.isnan(v)
                if m.sum() < 10:
                    continue
                rho = E.spearman(v[m], ex[m])
                if rho is not None:
                    ic[name].append((d, rho))
                # 十分位（因子值越大越好 → 9=最高）
                q = np.clip((rank_pct(v[m]) * 10).astype(int), 0, 9)
                exm = ex[m]
                for k in range(10):
                    vv = exm[q == k]
                    if len(vv):
                        dec[name][k].append(float(vv.mean()))
                # 池内 IC（只在本主题的候选池里）
                cm2 = m & (pk["cand"] if len(pk["cand"]) == len(cols) else np.zeros(len(cols), bool))
                if cm2.sum() >= 5:
                    r2 = E.spearman(v[cm2], ex[cm2])
                    if r2 is not None:
                        pools[name].append((d, r2))
        if len(ic["r60"]) % 200 == 0:
            print("[fac] %s 完成 (%d theme-day)" % (d, len(ic["r60"])), flush=True)

    res = {"window": [days[0], days[-1]], "hold": args.hold,
           "factors": {}, "deciles": {}, "deciles_in_cand": {}}
    for name, pairs in ic.items():
        st = E._stat(pairs, [x[1] for x in pairs])
        pos = float(np.mean([1.0 if x[1] > 0 else 0.0 for x in pairs]))
        row = dict(st, pos_rate=round(pos, 3))
        if pools.get(name):
            row["ic_in_cand"] = E._stat(pools[name], [x[1] for x in pools[name]])
        res["factors"][name] = row
        res["deciles"][name] = {int(k): round(float(np.mean(v)), 3) for k, v in sorted(dec[name].items())}
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=float)

    print("\n%-14s %6s %8s %8s %8s %8s   %s" % ("因子", "n", "IC均值", "IC中位", "为正比", "块状t", "十分位 0→9（同主题超额%）"))
    order = sorted(res["factors"].items(), key=lambda kv: -(kv[1].get("mean") or -9))
    for name, st in order:
        dd = res["deciles"].get(name) or {}
        dec_s = " ".join("%6.2f" % dd.get(k, float("nan")) for k in range(10))
        ica = st.get("ic_in_cand") or {}
        print("%-14s %6s %8.4f %8.4f %8.3f %8s   %s | 池内IC %s" % (
            name, st.get("n"), st.get("mean") or 0, st.get("median") or 0, st.get("pos_rate") or 0,
            st.get("block_t"), dec_s,
            ("%.4f(t=%s)" % (ica.get("mean"), ica.get("block_t"))) if ica.get("mean") is not None else "—"))
    print("\n[fac] 已写 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
