# -*- coding: utf-8 -*-
"""eval_replay_m5.py — 用**真 m5**（分时均价线）在 **428 条参考触发腿**上跑阶段 1 变体。

为什么：阶段 1 的验收样本（生产腿 n=33）太小、H1/H2 会变号；参考腿 n=428 跨度
2025-07-10 → 2026-08-31，是现成的、非重叠性更好的样本。此前它只有日线（V2 只能用"当日最低 <
前一日 VWAP"的假代理，已被真 m5 推翻）→ 本脚本用 `jobs/fetch_m5_replay.py` 预取的 m5 补齐。

变体（与 jobs/eval_exit_rules.py 同一实现，避免两套口径）：
  A_hold_t5   持 T+5
  V1          +3% 止盈，不到则持 T+5
  V2a/V2b     破分时黄线即走（单根 / 连续 2 根）
  V3a/V3b     +3% 与破线"先到先执行"（单根 / 2 根）
  V2c/V3c     只在他两个做 T 窗口内（09:45–10:00 / 14:00–14:30）判破线
  V2d/V3d     A1 口径：+3% 优先 + 破线需确认（0.5% 幅度 或 连续 2 根）

用法：
    .venv/bin/python jobs/fetch_m5_replay.py      # 先补 m5（幂等，中断可续）
    .venv/bin/python jobs/eval_replay_m5.py       # 出表 + 落 JSON
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import eval_leg_metrics as M  # noqa: E402

VARIANTS = {
    "A_hold_t5": None,
    "V1_tp3": None,
    "V2a_m5": "V2a", "V2b_m5": "V2b", "V3a_m5": "V3a", "V3b_m5": "V3b",
    "V2c_m5w": "V2c", "V3c_m5w": "V3c", "V2d_m5c": "V2d", "V3d_m5c": "V3d",
}


def _load_eval():
    spec = importlib.util.spec_from_file_location("_eer", os.path.join(HERE, "eval_exit_rules.py"))
    mod = importlib.util.module_from_spec(spec)
    old = sys.argv
    sys.argv = ["eval_exit_rules.py", "--accounts", "stock"]
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    finally:
        sys.argv = old
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, ".dsh-tmp", "wolfbt", "legmetrics",
                                                 "eval_replay_m5.json"))
    args = ap.parse_args()

    eer = _load_eval()
    legs = M.load_replay_legs()
    rb = M.ReplayBars()
    m5 = eer.M5Bars()
    print(f"[replay-m5] 参考腿 n={len(legs)}")

    out = {"_meta": {"n": len(legs)}, "variants": {}, "legs": [], "miss_m5": []}
    vals = {k: [] for k in VARIANTS}
    vals_ok = {k: [] for k in VARIANTS}          # 只在 m5 全齐的腿上比（apples-to-apples）
    n_ok = 0
    for l in legs:
        sym, d0, px = l["ts_code"], l["arm_date"], float(l["entry_px"] or 0)
        # 持有窗口（entry 起 5 个交易日）内 m5 是否齐全 —— 缺一天就把该腿排除在 m5 变体统计外
        rows = rb.get(sym)
        i = M.snap_idx(rb, sym, d0)
        hold_days = [rows[i + k][0] for k in range(1, 6) if i is not None and i + k < len(rows)]
        have = [d for d in hold_days if m5.day(sym, d)]
        m5_full = bool(hold_days) and len(have) == len(hold_days)
        n_ok += 1 if m5_full else 0
        rec = {"ts_code": sym, "arm_date": d0, "entry_px": px, "theme": l.get("theme"),
               "kind": l.get("kind"), "position": l.get("position"),
               "m5_days": "%d/%d" % (len(have), len(hold_days)), "m5_full": m5_full}
        miss = not m5_full
        for key, v in VARIANTS.items():
            if v is None:
                r, how = eer.sim_variant(rb, sym, d0, px, "A" if key == "A_hold_t5" else "V1")
            elif key.endswith("_m5c"):        # A1 口径（幅度/连续确认）
                r, how = eer.sim_variant_m5_confirmed(rb, m5, sym, d0, px, v)
            elif key.endswith("_m5w"):        # 只在他两个做 T 窗口内判破线
                r, how = eer.sim_variant_m5_window(rb, m5, sym, d0, px, v)
            else:
                r, how = eer.sim_variant_m5(rb, m5, sym, d0, px, v)
            rec[key] = r
            rec[key + "_how"] = how
            if how in ("no_m5", "missing_m5"):
                miss = True
            vals[key].append((d0, r))
            if m5_full:
                vals_ok[key].append((d0, r))
        if miss:
            out["miss_m5"].append({"ts_code": sym, "arm_date": d0, "m5_days": rec["m5_days"]})
        out["legs"].append(rec)
    out["_meta"]["n_m5_full"] = n_ok
    print("[replay-m5] m5 齐全的腿 %d/%d（其余腿的 m5 变体不进统计）" % (n_ok, len(legs)))

    def _subset(key):
        """m5 变体 → 只在 m5 齐全的腿上比；A/V1 → 全样本 + （sub）同子集基准。"""
        return vals_ok[key] if key not in ("A_hold_t5", "V1_tp3") else vals[key]

    def _hows(pairs, key):
        keep = {d for d, _ in pairs}
        cnt = {}
        for r in out["legs"]:
            if r["arm_date"] in keep and r.get("m5_full", True) == (key not in ("A_hold_t5", "V1_tp3")):
                h = r.get(key + "_how")
                cnt[h] = cnt.get(h, 0) + 1
        return cnt

    print(f"\n{'变体':<10} {'n':>4} {'胜率':>7} {'均值%':>7} {'中位%':>7} {'日度t':>7} {'块状t':>7}  出场方式")
    for k in VARIANTS:
        pairs = _subset(k)
        s = M.stat([v for _, v in pairs])
        bt = M.block_t(pairs)
        hows = _hows(pairs, k)
        out["variants"][k] = {"stat": s, "block_t": bt, "how": hows}
        print("%-10s %4s %7s %7s %7s %7s %7s  %s" % (
            k, s.get("n"), s.get("win"), s.get("mean"), s.get("median"),
            s.get("t"), (bt or {}).get("t"), hows))
    # 同一子集上的"日线口径"基准（apples-to-apples）
    print("\n【同子集基准（只在 m5 齐全的 %d 条腿上）】" % n_ok)
    for k in ("A_hold_t5", "V1_tp3"):
        s = M.stat([v for _, v in vals_ok[k]])
        if k not in out["variants"]:
            out["variants"][k] = {}
        out["variants"][k]["sub"] = s
        print("  %-10s n=%-4s 胜率 %-6s 均值 %-7s 中位 %-7s 块状t %s" % (
            k, s.get("n"), s.get("win"), s.get("mean"), s.get("median"),
            (M.block_t(vals_ok[k]) or {}).get("t")))

    # H1/H2（按 arm_date 中位切分）+ 分 kind/position 分档
    ds = sorted(str(l["arm_date"]) for l in legs)
    mid = ds[len(ds) // 2] if ds else "99999999"
    halves = {"H1": lambda d: str(d) < mid, "H2": lambda d: str(d) >= mid}
    out["halves"] = {"mid": mid}
    print("\n【H1/H2（按 arm_date 中位 %s 切）】" % mid)
    for k in ("A_hold_t5", "V1_tp3", "V2a_m5", "V3a_m5", "V3d_m5c"):
        row = {}
        for hn, fn in halves.items():
            sub = [v for (d, v) in _subset(k) if fn(d)]
            row[hn] = M.stat(sub)
        out["halves"][k] = row
        print("  %-10s H1 n=%-4s 胜率 %-6s 均值 %-7s | H2 n=%-4s 胜率 %-6s 均值 %-7s" % (
            k, row["H1"].get("n"), row["H1"].get("win"), row["H1"].get("mean"),
            row["H2"].get("n"), row["H2"].get("win"), row["H2"].get("mean")))
    print("\nm5 缺失（入场日）腿数: %d" % len(out["miss_m5"]))

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print("→ %s" % args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
