#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做T系统 · 建仓参数敏感度扫描（add-t-position-building tasks 7.3）。

对建仓分档初值做 ±30% 网格扫描，含"单票占比 × 总量上限 × MAX_FLOOR_SYMBOLS"联合网格
（design.md D3/M2b：实际并行票数 = min(MAX_FLOOR_SYMBOLS, 总量上限/单票占比)），
输出每参数可行区间与失效拐点，供 P4 固化为上线值。

用法（backend 目录下）：
    python scripts/t_build_params_scan.py [--json]

输出：stdout 打印扫描结果；--json 输出 JSON 便于落盘。
"""
import argparse
import json
import sys
from typing import Dict, List

# 待扫描建仓参数（分档初值，来自 add-t-position-building design.md D3）
PARAMS = {
    "single_order_pct": {"base": 0.05, "label": "单笔建仓 ≤ 净值", "unit": "%", "scale": 100},
    "per_symbol_cap": {"base": 0.15, "label": "单标底仓累计 ≤ 净值", "unit": "%", "scale": 100},
    "total_floor_cap": {"base": 0.55, "label": "总底仓 ≤ 净值", "unit": "%", "scale": 100},
    "max_floor_symbols": {"base": 10, "label": "组合标的宽松上限", "unit": "只", "scale": 1},
    "cand_score_min": {"base": 0.55, "label": "候选门槛", "unit": "分", "scale": 1},
    "build_score_min": {"base": 0.60, "label": "可建仓门槛", "unit": "分", "scale": 1},
    "vol_ratio_max": {"base": 2.0, "label": "量比上限（防追高）", "unit": "x", "scale": 1},
    "drawdown_min_pct": {"base": 1.0, "label": "回踩距高点回撤下限", "unit": "%", "scale": 1},
}

# 简化场景集（模拟盘校验：不同 regime × 底仓数 × 回转胜率）
SCENARIOS = [
    {"name": "震荡·满配回转", "regime": "ACTIVE", "floor_symbols": 6, "win_rate": 0.55, "amp": 3.0},
    {"name": "震荡·中配回转", "regime": "ACTIVE", "floor_symbols": 3, "win_rate": 0.5, "amp": 2.5},
    {"name": "谨慎·低配回转", "regime": "CAUTIOUS", "floor_symbols": 2, "win_rate": 0.45, "amp": 1.5},
    {"name": "下跌·只高抛", "regime": "HALT", "floor_symbols": 1, "win_rate": 0.35, "amp": 3.0},
]


def effective_symbols(total_cap: float, per_symbol: float, max_symbols: int) -> int:
    """M2b 口径：实际可并行底仓票数 = min(MAX_FLOOR_SYMBOLS, 总量上限/单票占比)。"""
    if per_symbol <= 0:
        return 0
    return max(1, min(int(total_cap / per_symbol), max_symbols))


def simulate(param_name: str, value: float, scenarios: List[dict],
             grid: Dict[str, float]) -> Dict[str, Dict[str, float]]:
    """简化回放：给定参数值，估算各场景下底仓可用度与回转日收益%。"""
    results = {}
    for sc in scenarios:
        regime = sc["regime"]
        # 档位取值（ACTIVE→std 基准，CAUTIOUS→保守 0.7x，HALT→保守 0.7x）
        tier_factor = 1.0 if regime == "ACTIVE" else 0.7
        single = grid["single_order_pct"] * tier_factor
        per_symbol = grid["per_symbol_cap"] * tier_factor
        total = grid["total_floor_cap"] * tier_factor

        # 实际可并行票数（联合口径）
        n_eff = effective_symbols(total, per_symbol, int(grid["max_floor_symbols"]))
        # 该场景理想票数
        ideal = sc["floor_symbols"]
        coverage = min(n_eff / max(ideal, 1), 1.0)

        # 参数影响
        if param_name == "total_floor_cap":
            total = value * tier_factor
            n_eff = effective_symbols(total, per_symbol, int(grid["max_floor_symbols"]))
            coverage = min(n_eff / max(ideal, 1), 1.0)
        if param_name == "per_symbol_cap":
            per_symbol = value * tier_factor
            n_eff = effective_symbols(total, per_symbol, int(grid["max_floor_symbols"]))
            coverage = min(n_eff / max(ideal, 1), 1.0)
        if param_name == "max_floor_symbols":
            n_eff = effective_symbols(total, per_symbol, int(value))
            coverage = min(n_eff / max(ideal, 1), 1.0)
        if param_name == "single_order_pct":
            single = value * tier_factor

        # 回转日收益估算：每笔价差 ≈ 振幅×0.3×净胜率 − 成本0.2%；每票每日约 1 笔有效回转
        per_trade_ret = sc["amp"] * 0.3 * (2 * sc["win_rate"] - 1) - 0.2
        daily_ret = per_trade_ret * coverage * ideal
        # 单笔上限约束（覆盖率再折扣：单笔过小则回转效率低）
        if param_name == "single_order_pct" and single < 0.03:
            daily_ret *= single / 0.03
        results[sc["name"]] = {
            "coverage": round(coverage, 2),
            "effective_symbols": n_eff,
            "daily_ret_pct": round(daily_ret, 3),
        }
    return results


def scan() -> Dict[str, Dict[str, any]]:
    out: Dict[str, Dict[str, any]] = {}
    grid = {k: v["base"] for k, v in PARAMS.items()}
    for name, meta in PARAMS.items():
        base = meta["base"]
        step = max(base * 0.1, 0.001)
        rows = []
        # ±30% 网格（5 档）
        for i in range(-3, 4):
            value = base + i * step
            if value <= 0:
                continue
            grid[name] = value
            sim = simulate(name, value, SCENARIOS, grid)
            # 失效拐点：覆盖率下滑 30% 以上，或日收益比基准差 0.05 个百分点以上（绝对差，兼容负收益）
            baseline = simulate(name, base, SCENARIOS, grid)
            degraded = any(
                s["coverage"] < b["coverage"] * 0.7
                or s["daily_ret_pct"] < b["daily_ret_pct"] - 0.05
                for s, b in zip(sim.values(), baseline.values())
            )
            rows.append({"value": round(value, 4), "degraded": degraded, "sim": sim})
        grid[name] = base  # 恢复
        feasible = [r for r in rows if not r["degraded"]]
        out[name] = {
            "label": meta["label"],
            "base": base,
            "unit": meta["unit"],
            "scan": rows,
            "feasible_range": (
                [round(rows[0]["value"], 4), round(rows[-1]["value"], 4)]
                if feasible else None
            ),
            "suggested": (
                round(feasible[-1]["value"], 4) if feasible else None
            ),
        }

    # 联合网格：单票占比 × 总量上限 × MAX_FLOOR_SYMBOLS
    joint = []
    for per in (0.10, 0.15, 0.20):
        for total in (0.40, 0.55, 0.70):
            for max_sym in (5, 10, 15):
                joint.append({
                    "per_symbol": per, "total_floor": total, "max_symbols": max_sym,
                    "effective_symbols_std": effective_symbols(total, per, max_sym),
                    "coverage_std": round(min(effective_symbols(total, per, max_sym) / 4.0, 1.0), 2),
                })
    out["_joint_grid"] = joint
    return out


def main():
    ap = argparse.ArgumentParser(description="做T建仓参数敏感度扫描")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()
    result = scan()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("=== 做T建仓参数敏感度扫描（±30% 网格，P4 标定用）===\n")
    for name, meta in result.items():
        if name.startswith("_"):
            continue
        print(f"◆ {meta['label']}（{name}）base={meta['base']}{meta['unit']}")
        print(f"  可行区间: {meta['feasible_range']} | 建议: {meta['suggested']}{meta['unit']}")
        for r in meta["scan"]:
            mark = "⚠️" if r["degraded"] else "  "
            print(f"  {mark} {r['value']}{meta['unit']} → " + ", ".join(
                f"{k}:{v['coverage']}/日收益{v['daily_ret_pct']}%" for k, v in r["sim"].items()))
    print("\n=== 联合网格（单票占比 × 总量上限 × MAX_FLOOR_SYMBOLS，M2b 口径）===")
    for j in result["_joint_grid"][:12]:
        print(f"  单票{j['per_symbol']} × 总量{j['total_floor']} × 上限{j['max_symbols']} → "
              f"实际并行 {j['effective_symbols_std']} 票（覆盖率 {j['coverage_std']}）")


if __name__ == "__main__":
    main()
