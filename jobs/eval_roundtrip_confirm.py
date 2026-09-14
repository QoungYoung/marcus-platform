# -*- coding: utf-8 -*-
"""eval_roundtrip_confirm.py — A1 确认条件的阈值敏感性（真 m5）。

A1（docs/exit-rules-m5-report.md §4）把 roundtrip_sell 改成「+3% 目标优先 + 破黄线需确认」，
确认条件两个旋钮：
  · WOLF_VWAP_BREAK_PCT   —— 跌破黄线幅度 ≥ pct 即算"突发"（默认 0.5%）
  · WOLF_VWAP_BREAK_ROUNDS —— 连续 rounds 轮（TMonitor 一轮 30s）在黄线下（默认 2）

本脚本用**真 m5** 在同一批生产腿上扫这两维，回答"阈值该定多少"。
结论（2026-09-14，n=33 探索样本）：**样本量不足以定阈值，阈值极度敏感** ——
    rounds=2/3 → 26/33 仍走破线，均值 −0.03 ~ −0.12；
    rounds=5    → 只剩 16/33 走破线，均值 +1.5（17 条改走 +3% 目标）。
    → 生产保持**保守默认**（0.5% / 2 轮）；等生产腿累积后再定。

用法：
    .venv/bin/python jobs/eval_roundtrip_confirm.py [--accounts stock,t] [--out .dsh-tmp/...]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import eval_leg_metrics as M  # noqa: E402

GRID_PCT = (0.0, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05)
GRID_ROUNDS = (2, 3, 5, 99)


def _load_eval():
    """按文件路径加载 jobs/eval_exit_rules.py（复用它已实现的 m5 变体）。"""
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
    ap.add_argument("--accounts", default="stock,t")
    ap.add_argument("--out", default=os.path.join(ROOT, ".dsh-tmp", "wolfbt", "legmetrics",
                                                "eval_roundtrip_confirm.json"))
    args = ap.parse_args()
    accts = tuple(a.strip() for a in args.accounts.split(",") if a.strip())

    eer = _load_eval()
    rows, _meta = M.fetch_paper_trades()
    legs, _o, _a, _d = M.build_legs(rows)
    strat = [l for l in legs
             if l["account"] in accts and l["entry_px"] and l["realized_pct"] is not None
             and abs(M._num(l.get("db_profit"))) > 1e-9]
    bars, M5 = eer.M.Bars(), eer.M5Bars()
    print(f"[confirm] 腿 n={len(strat)}（账户 {','.join(accts)}）")

    out = {"_meta": {"n": len(strat), "accounts": list(accts)}, "grid": []}
    print(f"{'pct':>6} {'rounds':>6} | {'破线出场':>8} {'+3%':>4} {'持T+5':>5} | "
          f"{'均值':>7} {'胜率':>6} {'中位':>7}")
    for pct in GRID_PCT:
        for rounds in GRID_ROUNDS:
            res, hows = [], {}
            for l in strat:
                r, how = eer.sim_variant_m5_confirmed(bars, M5, l["symbol"], l["entry_date"],
                                                      l["entry_px"], "V3d", pct=pct, rounds=rounds)
                if r is None:
                    continue
                res.append(r)
                hows[how] = hows.get(how, 0) + 1
            row = {"pct": pct, "rounds": rounds, "n": len(res),
                   "vwap_exit": hows.get("vwap_confirmed", 0), "tp3": hows.get("tp3", 0),
                   "hold": hows.get("hold_t5", 0),
                   "mean": round(st.mean(res), 2) if res else None,
                   "win": round(sum(1 for x in res if x > 0) / len(res), 4) if res else None,
                   "median": round(st.median(res), 2) if res else None}
            out["grid"].append(row)
            print(f"{pct:>6.3f} {rounds:>6} | {row['vwap_exit']:>8} {row['tp3']:>4} {row['hold']:>5} | "
                  f"{row['mean']:>7.2f} {row['win']:>6.2f} {row['median']:>7.2f}")
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(f"→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
