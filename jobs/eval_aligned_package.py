# -*- coding: utf-8 -*-
"""eval_aligned_package.py — **整包**回放：把已实现的买入层对齐改动**合起来**跟现行比（2026-09-15 round 9）。

用户要的顺序是「先对齐 → 再回测验证 → 最后才调参」。前面逐项都验过（v3 排序、C1 254 容差、F2 板块预过滤…），
本脚本回答**合起来**的问题：**把对齐项一起打开，路径 A 的布腿会比现在好还是差？**

对齐包（都是已实现、默认关的开关）：
  · **F2** `WOLF_PICK_BOARD_PREFILTER` —— 选股时就剔除无权限板块（创业板/科创板/北交所），用次优票补位；
  · **C1** `WOLF_DIP_PREVLOW_TOL=0.0` —— 254 挂单价回到他的话「就是挂前一天的低点」（现行历史自设 ×1.005）。

口径（与阶段 0 / `eval_wind_path_a.py` 同一把尺子，**离线复刻路径 A**）：
  候选域 = 链关键词 ∩ 生产 PG 概念表（剔 ST），leader = r60/amt20/lim 三分位均值，
  布腿 = leader 降序过位置闸（LOW/MID）取前 `--limit`（=3，与生产 `pick_buy(limit=3)` 一致）；
  **每条腿两种入场**（生产对同一只票同时挂 253 与 254 两条买腿）：
    253 = 次日收盘入场；254 = 触发价 low(D)×(1+tol)，次日盘中触及才成交；
  出场 = 入场后 `hold` 个交易日收盘；**净值 = 毛收益 − 往返成本**（生产自己的模型：
  佣金万0.86 单边 + 印花税 0.05% 卖出 + 过户费 0.001% 双边 + 滑点 0.0003 单边 = **0.1292%/往返**）。
  显著性：**按 (日,链) 配对**（同一天同一条链，对齐包 vs 现行包），ISO 周块状 t。

四个包（消融用）：
  A 现行        = 板块预过滤 OFF + tol 0.005
  B 只上 F2      = ON  + 0.005
  C 只上 C1      = OFF + 0.0
  D **对齐包**   = ON  + 0.0

用法::

    .venv/bin/python jobs/eval_aligned_package.py --hold 5
    .venv/bin/python jobs/eval_aligned_package.py --hold 10 --chains Chiplet概念,光刻机(胶)
"""
import argparse
import collections
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "jobs"), os.path.join(ROOT, "backend"),
           os.path.join(ROOT, "apps", "main_line"), os.path.join(ROOT, ".dsh-tmp", "wolfbt")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402

import eval_pick_selection as E  # noqa: E402
import eval_wind_path_a as W  # noqa: E402          # 路径 A 复刻的唯一实现（候选域/特征/位置闸）
from eval_leg_metrics import block_t  # noqa: E402

import importlib.util  # noqa: E402
_spec = importlib.util.spec_from_file_location("arm_pkg", os.path.join(ROOT, "jobs", "rotation_switch_arm.py"))
ARM = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ARM)

FEE_ROUNDTRIP = 0.1292      # %，生产口径（含滑点）—— 见 docs/wolf-buy-parameter-ledger.md §10
TOL_CUR = 0.005             # 现行历史自设
TOL_CORPUS = 0.0            # 他的话「就是挂前一天的低点」(2025-03-06)


def day_legs(panel, i, cols, limit, prefilter=False):
    """复刻 `pick_buy` 的 ②③ 段：leader 降序 → （可选）板块预过滤 → 位置闸 → 取前 limit。"""
    f = W._feat(panel, i, cols)
    keep = [k for k in range(len(cols)) if f["ok"][k]]
    if not keep:
        return []
    fc = [cols[k] for k in keep]
    lead = np.mean([W._pct_rank(np.nan_to_num(x, nan=-1e9))
                    for x in (f["r60"][keep], f["amt20"][keep], f["lim"][keep])], axis=0)
    order = sorted(range(len(fc)), key=lambda k: (-float(lead[k]), panel.codes[fc[k]]))
    out = []
    for k in order:
        if len(out) >= limit:
            break
        sym = panel.codes[fc[k]]
        if prefilter and not ARM.board_ok(sym):
            continue
        closes = [panel.close[j][fc[k]] for j in range(i + 1)]
        closes = [x for x in closes if x == x]
        if W.position_of(closes) in ("LOW", "MID"):
            out.append(fc[k])
    return out


def _leg_returns(panel, i, legs, hold, tol):
    """每条腿的 (253 收益, 254 收益(未成交=None))；净值已扣往返成本。"""
    r253 = panel.fwd_ret(i, hold, 1)
    r254, hit = panel.fwd_ret_254(i, hold, tol)
    out = []
    for j in legs:
        a = None if (r253 is None or r253[j] != r253[j]) else float(r253[j])
        b = None if (r254 is None or not hit[j] or r254[j] != r254[j]) else float(r254[j])
        out.append((None if a is None else a - FEE_ROUNDTRIP,
                    None if b is None else b - FEE_ROUNDTRIP))
    return out


def evaluate(panel, chains, cmap, bad, start, end, hold=5, limit=3):
    """四个包的 **(日,链) 级**收益 + 腿级明细。

    ⚠️ 关键口径（2026-09-15 修正）：**现行包 A 只含"今天真能布的腿"** —— 生产的
    `rotation_switch_arm` 会把无权限板块买腿**直接砍掉且不用次优票补位**（资金空着，收益 0），
    所以 A 不能拿"创业板选票的理论收益"当基准（那是买不到的收益）。
    """
    days = [d for d in panel.dates if start <= d <= end]
    recs, leg_detail, leg_detail_alt = [], [], []
    for chain, kws in sorted(chains.items()):
        kws = [W.norm(k) for k in (kws or []) if k]
        if not kws:
            continue
        cands = [ts for ts, cns in cmap.items()
                 if ts not in bad and any(k in W.norm(c) for k in kws for c in cns)]
        cols = [panel.ci[c] for c in cands if c in panel.ci][:W.SHORTLIST]
        if len(cols) < 2:
            continue
        for d in days:
            i = panel.di[d]
            if i < 61:
                continue
            legs_cur = day_legs(panel, i, cols, limit, prefilter=False)        # 现行选股（含无权限票）
            legs_ok = [j for j in legs_cur if ARM.board_ok(panel.codes[j])]    # 现行**实际能布**的腿
            legs_alt = day_legs(panel, i, cols, limit, prefilter=True)         # F2：选股时即剔除
            if not legs_ok and not legs_alt:
                continue
            rA = _leg_returns(panel, i, legs_ok, hold, TOL_CUR)
            rB = _leg_returns(panel, i, legs_alt, hold, TOL_CUR)
            rC = _leg_returns(panel, i, legs_ok, hold, TOL_CORPUS)
            rD = _leg_returns(panel, i, legs_alt, hold, TOL_CORPUS)
            # 腿级明细（C1 用：同一条腿在两种 tol 下的 254 结果）
            for k, j in enumerate(legs_ok):        # 现行**能布**的腿：rA=tol 0.005, rC=tol 0
                leg_detail.append({"date": d, "chain": chain, "sym": panel.codes[j],
                                   "r_tol005": rA[k][1] if k < len(rA) else None,
                                   "r_tol0": rC[k][1] if k < len(rC) else None})
            for k, j in enumerate(legs_alt):       # F2 后的腿：rB=tol 0.005, rD=tol 0
                leg_detail_alt.append({"date": d, "chain": chain, "sym": panel.codes[j],
                                       "r_tol005": rB[k][1] if k < len(rB) else None,
                                       "r_tol0": rD[k][1] if k < len(rD) else None})

            def _pk(rows, which):
                v = [x[which] for x in rows if x[which] is not None]
                return None if not v else float(np.mean(v))

            recs.append({
                "date": d, "chain": chain,
                "n_raw": len(legs_cur), "n_A": len(legs_ok), "n_B": len(legs_alt),
                "dropped": [panel.codes[j] for j in legs_cur if j not in legs_ok],
                "sym_A": [panel.codes[j] for j in legs_ok],
                "sym_B": [panel.codes[j] for j in legs_alt],
                "A": {"253": _pk(rA, 0), "254": _pk(rA, 1)},
                "B": {"253": _pk(rB, 0), "254": _pk(rB, 1)},
                "C": {"253": _pk(rC, 0), "254": _pk(rC, 1)},
                "D": {"253": _pk(rD, 0), "254": _pk(rD, 1)},
            })
    return recs, leg_detail, leg_detail_alt


def summarize(recs, pkg, key):
    pairs = [(r["date"], r[pkg][key]) for r in recs if r[pkg][key] is not None]
    vals = [v for _, v in pairs]
    if not vals:
        return {"n": 0}
    a = np.array(vals, dtype=float)
    bt = block_t(pairs)
    return {"n": len(a), "mean": round(float(a.mean()), 3), "median": round(float(np.median(a)), 3),
            "win": round(float((a > 0).mean()), 3),
            "t": round(float(a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))), 2)
            if len(a) > 1 and a.std(ddof=1) > 0 else None,
            "block_t": bt.get("t"), "blocks": bt.get("blocks")}


def paired(recs, pkg_a, pkg_b, key):
    pairs = [(r["date"], r[pkg_b][key] - r[pkg_a][key]) for r in recs
             if r[pkg_b][key] is not None and r[pkg_a][key] is not None]
    vals = [v for _, v in pairs]
    if not vals:
        return {"n": 0}
    a = np.array(vals, dtype=float)
    bt = block_t(pairs)
    return {"n": len(a), "mean": round(float(a.mean()), 3),
            "win": round(float((a > 0).mean()), 3), "block_t": bt.get("t"), "blocks": bt.get("blocks")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chains", default="", help="逗号分隔；默认全部（缓存里的所有子方向）")
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260911")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--bars", default=E.BARS)
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_aligned_package.json"))
    args = ap.parse_args()

    chains = W.load_chains()
    if args.chains:
        want = [x.strip() for x in args.chains.split(",") if x.strip()]
        chains = {k: v for k, v in chains.items() if k in want}
    cmap, bad = W.load_concepts()
    panel = E.Panel(args.bars)
    print("[pkg] 链 %d | %s→%s | hold=%d limit=%d | 往返费率 %.4f%% | PG 票 %d"
          % (len(chains), args.start, args.end, args.hold, args.limit, FEE_ROUNDTRIP, len(cmap)))

    cache = {}
    for hold in ([args.hold] if args.hold else [5, 10]):
        recs, leg_cur, leg_alt = evaluate(panel, chains, cmap, bad, args.start, args.end,
                                         hold=hold, limit=args.limit)
        cache["hold_%d" % hold] = recs
        print("\n===== hold=%d  （(日,链) 样本 %d） =====" % (hold, len(recs)))
        names = {"A": "现行(能布的腿,tol0.005)", "B": "只上F2(板块预过滤)",
                 "C": "只上C1(tol=0)", "D": "**对齐包**(F2+C1)"}
        for pkg in ("A", "B", "C", "D"):
            s253, s254 = summarize(recs, pkg, "253"), summarize(recs, pkg, "254")
            print("  %-22s 253: %s" % (names[pkg], s253))
            print("  %-22s 254: %s" % ("", s254))
        for pkg in ("B", "C", "D"):
            print("  Δ(%s − 现行)  253: %s | 254: %s"
                  % (names[pkg][:12], paired(recs, "A", pkg, "253"), paired(recs, "A", pkg, "254")))
        chg = sum(1 for r in recs if r["sym_A"] != r["sym_B"])
        nb_drop = sum(1 for r in recs if r["dropped"])
        print("  腿数：现行选出 %.1f 条/日链（其中无权限被丢 %.1f 条 → 实际能布 %.1f 条）| F2 后 %.1f 条"
              % (np.mean([r["n_raw"] for r in recs]), np.mean([r["n_raw"] - r["n_A"] for r in recs]),
                 np.mean([r["n_A"] for r in recs]), np.mean([r["n_B"] for r in recs])))
        print("  有腿被丢的 (日,链)：%d / %d（%.1f%%）；选票被 F2 改变的 (日,链)：%d（%.1f%%）"
              % (nb_drop, len(recs), 100.0 * nb_drop / max(len(recs), 1), chg, 100.0 * chg / max(len(recs), 1)))
        # ── C1 腿级：**同一批腿**在两种 tol 下（未成交按 0 计 = 资金空着）→ 每条已挂腿的期望 ──
        tot = len(leg_cur)
        f005 = [x for x in leg_cur if x["r_tol005"] is not None]
        f0 = [x for x in leg_cur if x["r_tol0"] is not None]
        common = [x for x in leg_cur if x["r_tol005"] is not None and x["r_tol0"] is not None]
        extra = [x for x in leg_cur if x["r_tol005"] is not None and x["r_tol0"] is None]
        if tot:
            exp005 = float(np.mean([x["r_tol005"] or 0.0 for x in leg_cur]))
            exp0 = float(np.mean([x["r_tol0"] or 0.0 for x in leg_cur]))
            print("  C1 254（现行能布的 %d 条腿，未成交=0 即资金空着）：" % tot)
            print("     tol=0.005(现行)：成交 %d 条(%.1f%%) 成交均收益 %+.3f%% → **每条已挂腿期望 %+.3f%%**"
                  % (len(f005), 100.0 * len(f005) / tot,
                     float(np.mean([x["r_tol005"] for x in f005])) if f005 else 0.0, exp005))
            print("     tol=0(他的话)  ：成交 %d 条(%.1f%%) 成交均收益 %+.3f%% → **每条已挂腿期望 %+.3f%%**"
                  % (len(f0), 100.0 * len(f0) / tot,
                     float(np.mean([x["r_tol0"] for x in f0])) if f0 else 0.0, exp0))
            if common:
                d = np.array([x["r_tol0"] - x["r_tol005"] for x in common], dtype=float)
                print("     两档都成交的 %d 条：tol=0 的入场价更低 → 逐腿 %+.3f%%（**机制性差价≈0.5%%，不是 alpha**）"
                      % (len(common), float(d.mean())))
            if extra:
                v = np.array([x["r_tol005"] for x in extra], dtype=float)
                print("     只有 tol=0.005 才成交的 %d 条（他那档会错过）：平均 %+.3f%%（中位 %+.3f%%、胜率 %.0f%%）"
                      % (len(v), float(v.mean()), float(np.median(v)), 100.0 * float((v > 0).mean())))
            print("     → 期望差(tol0 − 现行) %+.3fpp/条腿" % (exp0 - exp005))

    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    json.dump({k: {"n": len(v), "recs": v, "legs_cur": leg_cur, "legs_alt": leg_alt}
               for k, v in cache.items()},
              open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n[pkg] 写出 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
