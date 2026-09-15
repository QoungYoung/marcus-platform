# -*- coding: utf-8 -*-
"""eval_pick_rank_v3.py — 「条件化分层排序 v3」离线验收（2026-09-15）。

设计：`apps/main_line/wolf_pick_rank_v3.py`（单一实现，生产将复用同一函数）。
本脚本只做**对照**：同一批 主题×日 上，v3 的选票 vs 现行 leader 的选票（tier1），
尺子与 `docs/leg-metrics-spec.md` 一致（次日收盘入场、持 5/20 日、同主题同日等权超额、块状 t、H1/H2）。
**配对**：两臂在同一 theme-day 上算差（v3 − leader），避免样本差造成的错觉。

用法：
  .venv/bin/python jobs/eval_pick_rank_v3.py --start 20260105 --end 20260904 --hold 5 \
      --variants V1,V2,V3 [--json out.json]
"""
import argparse
import collections
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "apps", "main_line"))

import eval_pick_selection as E          # noqa: E402
import eval_pick_factors as F            # noqa: E402
import wolf_pick_rank_v3 as V3           # noqa: E402


def _board_ok(ts):
    """账户权限过滤：默认剔除创业板(300/301)、科创板(688)、北交所（= 生产 WOLF_PICK_BOARD_EXCLUDE）。"""
    code, mkt = str(ts).split(".")[0], str(ts).split(".")[-1].upper()
    if mkt == "SZ" and code[:3] in ("300", "301"):
        return False
    if mkt == "SH" and code.startswith("688"):
        return False
    if mkt == "BJ" or code[:3] == "920":
        return False
    return True


def _mean(vals):
    xs = [float(v) for v in vals if v is not None and v == v]
    return float(np.mean(xs)) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260105")
    ap.add_argument("--end", default="20260904")
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--variants", default="V1,V2,V3")
    ap.add_argument("--components", default="", help="组件消融（逗号分隔，如 pos / pos,dist / flat,hist）；默认全开")
    ap.add_argument("--board-filter", action="store_true",
                    help="剔除无权限板块（cyb=300/301, kcb=688, bj）——对齐 WOLF_PICK_BOARD_EXCLUDE 的生产可执行域")
    ap.add_argument("--hard-filters", default="",
                    help="候选域硬过滤（逗号分隔）：flatmed(横盘天数≥本主题当日中位) / mf5pos(近5日资金非净流出) / noout5(当日不净流出) / lowonly")
    ap.add_argument("--stage", default="none", choices=["none", "qtile", "pool"],
                    help="阶段口径：none / qtile(主题r5在13主题中的分位) / pool(方向层池内=强主题)")
    ap.add_argument("--qtile-hi", type=float, default=0.66, help="stage=qtile 的强主题阈值（主题 r5 跨主题分位）")
    ap.add_argument("--qtile-lo", type=float, default=0.33, help="stage=qtile 的弱主题阈值")
    ap.add_argument("--diergong", action="store_true",
                    help="强主题按他的『二供』口径：跳过主题内第1名、取第2–4名")
    ap.add_argument("--tiebreak", default="ts", choices=["ts", "dist", "flat", "rs", "amt"],
                    help="并列时的次级键（默认 ts 仅作稳定序；防'靠代码序'的伪结论）")
    ap.add_argument("--limit", type=int, default=2, help="每个主题取前 n（与 tier1 的 2 对齐）")
    ap.add_argument("--by-rank", action="store_true",
                    help="额外输出『主题内第 k 只腿』与『前 k 只腿组合』的期望表（验收主题内腿数上限）")
    ap.add_argument("--by-rank-var", default="V3", help="--by-rank 用哪个变体（默认 V3）")
    ap.add_argument("--by-drawdown", action="store_true",
                    help="按『当日回撤幅度(高-收)/高』与『收盘在当日区间的位置』分桶看期望（候选(d) 回踩门槛）")
    ap.add_argument("--by-drawdown-var", default="V3", help="--by-drawdown 用哪个变体（默认 V3）")
    ap.add_argument("--themes", default="")
    ap.add_argument("--domain", default="lowmid", choices=["lowmid", "cand_low", "cand_lowonly"],
                    help="候选域：lowmid=现行 tier1 域 / cand_low=候选池∩LOW,MID（去掉 r20≥0,rs≥0 闸）/ cand_lowonly=仅 LOW")
    ap.add_argument("--dist-max", type=float, default=None, help="额外限制 dist_prevlow ≤ x%%（默认不限）")
    ap.add_argument("--fee-pct", type=float, default=0.0,
                    help="单次往返总成本（小数，如 0.000692=佣金双边+印花税+过户费；0=不计费）")
    ap.add_argument("--json", default=os.path.join(ROOT, ".dsh-tmp", "buyside", "eval_rank_v3.json"))
    args = ap.parse_args()
    E.HOLD = int(args.hold)
    variants = [x.strip().upper() for x in args.variants.split(",") if x.strip()]

    panel = E.Panel()
    MF = E.load_moneyflow(panel, "moneyflow")

    uni, lead, allc, MS = E.load_universe()
    th_cons, cmap = E.theme_concept_sets()
    want = [t for t in uni if not args.themes or t in args.themes.split(",")]
    days = [d for d in panel.dates if args.start <= d <= args.end and panel.di[d] >= 260]
    pool_by_day = {}
    if args.stage == "pool":
        import pandas as _pd
        pctw = _pd.DataFrame(panel.pct, index=panel.dates, columns=panel.codes)
        amtw = _pd.DataFrame(panel.amt, index=panel.dates, columns=panel.codes).fillna(0.0)
        px = {d: {c: float(v) for c, v in pctw.loc[d].dropna().items()} for d in panel.dates}
        mamt5 = amtw.sum(axis=1).rolling(5).sum()
        for d in days:
            i = panel.di[d]
            share5 = {}
            for th, codes in uni.items():
                cc = [c for c in codes if c in amtw.columns]
                if not cc:
                    continue
                ser = amtw[cc].sum(axis=1).rolling(5).sum() / mamt5.replace(0, np.nan)
                v = ser.iloc[i]
                if v == v:
                    share5[th] = float(v)
            res = MS.score_day(panel.dates, i, px, uni, lead, allc, share5=share5, pool_k=3)
            pool_by_day[d] = set(res.get("pool") or [])
        print("[v3] 方向层池已回放（%d 天）" % len(pool_by_day), flush=True)

    print("[v3] %d 天 × %d 主题 | hold=%d | 变体 %s | 资金流=%s"
          % (len(days), len(want), args.hold, variants, "有" if MF is not None else "无"), flush=True)

    # 跨主题的 r5 分位（阶段口径用；PIT：只用当日及以前的日线）
    r5_by_day_theme = {}
    for d in days:
        i = panel.di[d]
        vals = {}
        for th in want:
            codes = uni.get(th) or []
            jj = np.array([panel.ci[c] for c in codes if c in panel.ci])
            if len(jj) == 0:
                continue
            with np.errstate(invalid="ignore"):
                r5v = np.nanmean((panel.close[i, jj] / panel.close[i - 5, jj] - 1.0) * 100.0)
            if r5v == r5v:
                vals[th] = float(r5v)
        if vals:
            arr = np.array(list(vals.values()))
            order = np.argsort(np.argsort(arr)) / max(1, len(arr) - 1)
            r5_by_day_theme[d] = {t: float(order[k]) for k, t in enumerate(vals)}

    by_rank = collections.defaultdict(list)   # rank k → [(date, ex), ...]
    topk = collections.defaultdict(list)      # 前 k 只等权 → [(date, ex), ...]
    dd_bucket = collections.defaultdict(list)   # 回撤幅度桶 → [(date, ex)]
    pos_bucket = collections.defaultdict(list)  # 当日区间位置桶 → [(date, ex)]
    recs = []          # 每个 (日,主题) 一行：各臂的均值 + 配对数
    for d in days:
        i = panel.di[d]
        r_fwd = panel.fwd_ret(i, args.hold, 1)
        if r_fwd is None:
            continue
        for th in want:
            codes = uni.get(th) or []
            try:
                pk = E.pick_day(panel, i, th, uni, th_cons, cmap)
            except Exception as e:
                print("[v3] pick_day err", d, th, str(e)[:60], flush=True)
                continue
            if pk is None or pk["n"] < 10:
                continue
            cols = pk["cols"]
            basket = np.nanmean(r_fwd[[panel.ci[c] for c in codes if c in panel.ci]])
            ex = r_fwd[cols] - basket
            Fx = F.factor_matrix(panel, i, cols, None)
            mf1 = MF[i, cols] if MF is not None else np.full(len(cols), np.nan)
            mf5 = np.nansum(MF[max(0, i - 4):i + 1][:, cols], axis=0) if MF is not None else np.full(len(cols), np.nan)
            r20 = np.asarray(Fx["r20"], dtype=float)
            r5 = np.asarray(Fx["r5"], dtype=float)
            rs = np.asarray(Fx["cs_rs"], dtype=float) if "cs_rs" in Fx else r20 - np.nanmean(r20)
            theme_r5 = float(np.nanmean(r5))
            theme_r20 = float(np.nanmean(r20))
            # 主题内强度分位（rank_in_theme：1=最强）
            rk = E._nan_pct_rank(np.where(np.isnan(r20), -1e18, r20))
            subs = list(pk["subs"])
            rows = []
            for k, ts in enumerate(subs):
                rows.append({"ts": ts, "pos": str(pk["pos"][k]), "dist_prevlow": float(pk["dist_prevlow"][k]),
                             "r20": float(r20[k]) if r20[k] == r20[k] else None,
                             "rs": float(rs[k]) if rs[k] == rs[k] else None,
                             "flat_low_days": float(Fx["flat_low_days"][k]),
                             "hist": float(Fx["hist"][k]),
                             "mf1": float(mf1[k]) if mf1[k] == mf1[k] else None,
                             "mf5": float(mf5[k]) if mf5[k] == mf5[k] else None,
                             "vol_ratio5": float(Fx["vol_ratio5"][k]) if Fx["vol_ratio5"][k] == Fx["vol_ratio5"][k] else None,
                             "pct_today": float(Fx["pct_today"][k]) if Fx["pct_today"][k] == Fx["pct_today"][k] else None,
                             "rank_in_theme": float(rk[k]),
                             "cand": bool(pk["cand"][k]), "lowmid": bool(pk["lowmid"][k]),
                             "ex": float(ex[k]) if ex[k] == ex[k] else None,
                             "rf": float(r_fwd[cols][k]) if r_fwd[cols][k] == r_fwd[cols][k] else None})
            # 域（--domain）：lowmid=现行 tier1 的来源域；cand_low=候选池∩LOW/MID（去掉 r20≥0/rs≥0 两个负贡献闸）；
            # cand_lowonly=候选池∩LOW（实测 LOW −0.132% vs MID −0.484%）
            if args.domain == "lowmid":
                base_rows = [r for r in rows if r["lowmid"]]
            elif args.domain == "cand_low":
                base_rows = [r for r in rows if r["cand"] and r["pos"] in ("LOW", "MID")]
            else:
                base_rows = [r for r in rows if r["cand"] and r["pos"] == "LOW"]
            if args.dist_max is not None:
                base_rows = [r for r in base_rows if r["dist_prevlow"] is not None
                             and r["dist_prevlow"] <= args.dist_max]
            if args.board_filter:
                base_rows = [r for r in base_rows if _board_ok(r["ts"])]
            hf = [x.strip() for x in args.hard_filters.split(",") if x.strip()]
            if hf:
                fv = [r["flat_low_days"] for r in base_rows if r.get("flat_low_days") is not None]
                fmed = float(np.median(fv)) if fv else None
                keep = []
                for r in base_rows:
                    ok = True
                    if "flatmed" in hf and fmed is not None and (r.get("flat_low_days") or 0) < fmed:
                        ok = False
                    if "mf5pos" in hf and not (r.get("mf5") is not None and r["mf5"] > 0):
                        ok = False
                    if "noout5" in hf and not (r.get("mf1") is not None and r["mf1"] > 0):
                        ok = False
                    if "lowonly" in hf and r["pos"] != "LOW":
                        ok = False
                    if ok:
                        keep.append(r)
                base_rows = keep
            # 阶段（强/弱主题）与「二供」取法
            tq = (r5_by_day_theme.get(d) or {}).get(th)
            if args.stage == "pool":
                strong = th in (pool_by_day.get(d) or set())
                weak = not strong
            elif args.stage == "qtile":
                strong = tq is not None and tq >= args.qtile_hi
                weak = tq is not None and tq <= args.qtile_lo
            else:
                strong = weak = False
            # 阶段/二供**委托模块**（单一实现）；eval 只保留域级（板块/硬过滤）与观测
            cur = [rows[k] for k in np.where(pk["t1"])[0]]        # 现行 leader 的 tier1
            if args.by_rank and args.by_rank_var.upper() == "CUR" and cur:
                _srt = sorted(cur, key=lambda x: -(x.get("r20") if x.get("r20") is not None else -1e18))
                for _k, _p in enumerate(_srt[:6]):
                    by_rank[_k].append((d, _p["ex"]))
                    topk[_k + 1].append((d, _mean([q["ex"] for q in _srt[:_k + 1]])))
            out = {"d": d, "th": th, "n_base": len(base_rows), "n_cur": len(cur),
                   "theme_r5": theme_r5, "theme_r20": theme_r20,
                   "v_cur": _mean([c["rf"] for c in cur]),
                   "x_cur": _mean([c["ex"] for c in cur])}
            for var in variants:
                picks = V3.pick_top(base_rows, theme_r5=theme_r5, n=args.limit, variant=var,
                                    components=args.components or None, tiebreak=args.tiebreak,
                                    theme_r5_qtile=tq, stage_mode=args.stage,
                                    diergong=args.diergong,
                                    qtile_hi=args.qtile_hi, qtile_lo=args.qtile_lo)
                out["v_" + var] = _mean([p["rf"] for p in picks])
                out["x_" + var] = _mean([p["ex"] for p in picks])
                if args.by_rank and var == args.by_rank_var.upper() and picks:
                    for _k, _p in enumerate(picks[:6]):
                        by_rank[_k].append((d, _p["ex"]))
                        topk[_k + 1].append((d, _mean([q["ex"] for q in picks[:_k + 1]])))
                if args.by_drawdown and var == args.by_drawdown_var.upper() and picks:
                    for _p in picks:
                        _j = panel.ci.get(_p["ts"])
                        if _j is None:
                            continue
                        _hi = panel.high[i, _j]
                        _lo = panel.low[i, _j]
                        _cl = panel.close[i, _j]
                        if not (_hi > 0 and _cl == _cl and _lo == _lo):
                            continue
                        _dd = (_hi - _cl) / _hi * 100.0                      # 当日回撤(%)
                        _pos = (_cl - _lo) / (_hi - _lo) if _hi > _lo else None  # 收盘位置 0=最低 1=最高
                        _lbl = ("dd<1" if _dd < 1 else "dd1-2" if _dd < 2 else
                                "dd2-3" if _dd < 3 else "dd3-5" if _dd < 5 else "dd>=5")
                        dd_bucket[_lbl].append((d, _p["ex"]))
                        if _pos is not None:
                            _pl = ("pos<0.2" if _pos < 0.2 else "pos0.2-0.5" if _pos < 0.5 else
                                   "pos0.5-0.8" if _pos < 0.8 else "pos>=0.8")
                            pos_bucket[_pl].append((d, _p["ex"]))
                out["n_" + var] = len(picks)
                # 与现行 tier1 的重合度
                if cur and picks:
                    sc = {x["ts"] for x in cur}
                    out["ov_" + var] = len(sc & {p["ts"] for p in picks}) / max(1, len(picks))
                else:
                    out["ov_" + var] = None
            recs.append(out)
    if not recs:
        print("[v3] 无样本")
        return 1

    def st(rows, key, date_key="d"):
        return E._stat([(r[date_key], r.get(key)) for r in rows], [r.get(key) for r in rows])

    # 费率：对每条 pick（=一次往返）扣同一个成本 → 只改水平，不改配对差
    FEE = float(args.fee_pct or 0.0) * 100.0        # 转成百分点
    if FEE:
        for r in recs:
            for k in list(r.keys()):
                if (k.startswith("v_") or k.startswith("x_")) and isinstance(r.get(k), (int, float)):
                    r[k] = r[k] - FEE
    res = {"window": [days[0], days[-1]], "hold": args.hold, "n_theme_days": len(recs), "variants": variants,
           "fee_pct": float(args.fee_pct or 0.0),
           "domain": args.domain, "dist_max": args.dist_max, "limit": args.limit,
           "board_filter": bool(args.board_filter), "hard_filters": args.hard_filters,
           "stage": args.stage, "diergong": bool(args.diergong), "tiebreak": args.tiebreak,
           "qtile_hi": args.qtile_hi, "qtile_lo": args.qtile_lo,
           "arms": {}, "paired_vs_cur": {}, "seg": {}}
    res["arms"]["cur"] = {"value": st(recs, "v_cur"), "excess": st(recs, "x_cur"),
                          "stock_n": int(sum(r["n_cur"] or 0 for r in recs))}
    for var in variants:
        res["arms"][var] = {"value": st(recs, "v_" + var), "excess": st(recs, "x_" + var),
                            "stock_n": int(sum(r["n_" + var] or 0 for r in recs)),
                            "overlap_with_cur": round(float(np.nanmean([r["ov_" + var] for r in recs
                                                                       if r.get("ov_" + var) is not None])), 3)}
        diff = [(r["d"], (r["x_" + var] - r["x_cur"])) for r in recs
                if r.get("x_" + var) is not None and r.get("x_cur") is not None]
        res["paired_vs_cur"][var] = E._stat(diff, [x[1] for x in diff])
    mid = sorted(r["d"] for r in recs)[len(recs) // 2]
    for tag, sel in (("H1", [r for r in recs if r["d"] < mid]), ("H2", [r for r in recs if r["d"] >= mid])):
        res["seg"][tag] = {"split_at": mid, "n": len(sel)}
        for a, key in [("cur", "x_cur")] + [(v, "x_" + v) for v in variants]:
            res["seg"][tag][a] = st(sel, key)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=float)

    print("\n== 各臂（theme-day 等权；同主题超额%）==")
    for a, v in res["arms"].items():
        ex = v["excess"]
        print("   %-5s n=%-5s 超额 mean=%7.3f median=%7.3f win=%.3f block_t=%5s | 只数 %-5s 与现行重合 %s"
              % (a, ex.get("n"), ex.get("mean") or 0, ex.get("median") or 0, ex.get("win") or 0,
                 ex.get("block_t"), v["stock_n"], v.get("overlap_with_cur", "—")))
    print("\n== 配对（v3 − 现行 leader，同 theme-day）==")
    for k, v in res["paired_vs_cur"].items():
        print("   %-5s n=%-5s Δ=%7.3f block_t=%5s" % (k, v.get("n"), v.get("mean") or 0, v.get("block_t")))
    print("\n== H1/H2 ==")
    for tag in ("H1", "H2"):
        seg = res["seg"][tag]
        print("   %s(切%s) %s" % (tag, seg["split_at"],
                                  " ".join("%s=%s" % (a, (seg[a].get("mean") if seg[a].get("n") else None))
                                           for a in ["cur"] + variants)))
    if args.by_drawdown:
        print("\n== 按『当日回撤幅度 (高-收)/高』分桶（变体 %s；同主题超额%%）==" % args.by_drawdown_var.upper())
        for lbl in ("dd<1", "dd1-2", "dd2-3", "dd3-5", "dd>=5"):
            rows_k = dd_bucket.get(lbl) or []
            if not rows_k:
                continue
            stt = E._stat(rows_k, [v for _, v in rows_k])
            print("   %-7s n=%-5s 超额 mean=%7.3f median=%7.3f win=%.3f block_t=%5s"
                  % (lbl, stt.get("n"), stt.get("mean") or 0, stt.get("median") or 0,
                     stt.get("win") or 0, stt.get("block_t")))
        print("   —— 对照：若门槛提到 x，被砍掉的是 dd<x 的那些腿 ——")
        for _cut, _lbls in ((2.0, ("dd<1",)), (3.0, ("dd<1", "dd1-2")), (5.0, ("dd<1", "dd1-2", "dd2-3"))):
            drop = [x for _l in _lbls for x in (dd_bucket.get(_l) or [])]
            if drop:
                stt = E._stat(drop, [v for _, v in drop])
                print("   砍掉 dd<%d%% 的腿：n=%-5s 其超额 mean=%7.3f median=%7.3f block_t=%5s"
                      % (_cut, stt.get("n"), stt.get("mean") or 0, stt.get("median") or 0, stt.get("block_t")))
        print("\n== 按『收盘在当日区间的位置』分桶（0=收在最低 1=收在最高）==")
        for lbl in ("pos<0.2", "pos0.2-0.5", "pos0.5-0.8", "pos>=0.8"):
            rows_k = pos_bucket.get(lbl) or []
            if not rows_k:
                continue
            stt = E._stat(rows_k, [v for _, v in rows_k])
            print("   %-11s n=%-5s 超额 mean=%7.3f median=%7.3f win=%.3f block_t=%5s"
                  % (lbl, stt.get("n"), stt.get("mean") or 0, stt.get("median") or 0,
                     stt.get("win") or 0, stt.get("block_t")))
        res["by_drawdown"] = {k: E._stat(v, [x for _, x in v]) for k, v in dd_bucket.items()}
        res["by_pos"] = {k: E._stat(v, [x for _, x in v]) for k, v in pos_bucket.items()}
    if args.by_rank:
        print("\n== 主题内第 k 只腿（变体 %s；同主题超额%%）==" % args.by_rank_var.upper())
        for k in sorted(by_rank):
            rows_k = by_rank[k]
            stt = E._stat(rows_k, [v for _, v in rows_k])
            print("   第%d只 n=%-5s 超额 mean=%7.3f median=%7.3f win=%.3f block_t=%5s"
                  % (k + 1, stt.get("n"), stt.get("mean") or 0, stt.get("median") or 0,
                     stt.get("win") or 0, stt.get("block_t")))
        print("\n== 前 k 只腿等权组合（theme-day 等权）==")
        for k in sorted(topk):
            rows_k = topk[k]
            stt = E._stat(rows_k, [v for _, v in rows_k])
            print("   top%d   n=%-5s 超额 mean=%7.3f median=%7.3f win=%.3f block_t=%5s"
                  % (k, stt.get("n"), stt.get("mean") or 0, stt.get("median") or 0,
                     stt.get("win") or 0, stt.get("block_t")))
        res["by_rank"] = {str(k + 1): E._stat(v, [x for _, x in v]) for k, v in by_rank.items()}
        res["topk"] = {str(k): E._stat(v, [x for _, x in v]) for k, v in topk.items()}
    print("\n[v3] 已写 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
