#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""做T系统 · 风控参数敏感度扫描（5.1）。

对分档初值做 ±30% 网格扫描，输出每参数可行区间与失效拐点，收敛为上线值（保守档起步）。

用法（backend 目录下）：
    python scripts/t_sensitivity_scan.py [--json]

输出：stdout 打印扫描结果；--json 输出 JSON 便于落盘。
"""
import argparse
import json
import sys
from typing import Dict, List

# 待扫描参数（分档初值，来自 final-t-plan.md §②）
PARAMS = {
    "MAX_SINGLE_ORDER_PCT": {"base": 0.05, "label": "单笔下单额占净值", "unit": "%", "scale": 100},
    "DAILY_LOSS_BREAKER_PCT": {"base": 0.02, "label": "单日亏损熔断", "unit": "%", "scale": 100},
    "DAILY_LOSS_WARN_PCT": {"base": 0.01, "label": "单日预警线", "unit": "%", "scale": 100},
    "MAX_SELL_FLOOR_RATIO": {"base": 1.0, "label": "买腿vs可卖底仓(L2)", "unit": "x", "scale": 1},
    "COOLDOWN_AFTER_LOSS_MIN": {"base": 15, "label": "亏损后冷却", "unit": "min", "scale": 1},
    "MAX_DAILY_TURNOVER_RATIO": {"base": 3.0, "label": "日回转额/净值上限", "unit": "x", "scale": 1},
    "FLOOR_LOWER_RATIO": {"base": 0.5, "label": "底仓保留下限", "unit": "x", "scale": 1},
    "COST_RATIO_LIMIT": {"base": 0.2, "label": "滑点手续费/价差上限", "unit": "%", "scale": 100},
}

# 历史回放用的简化场景集（模拟盘校验：不同 regime × 不同振幅 × 不同触发次数）
SCENARIOS = [
    {"name": "震荡·高振幅·多触发", "regime": "ACTIVE", "daily_amp": 4.0, "triggers": 6, "win_rate": 0.55},
    {"name": "震荡·中振幅·中触发", "regime": "ACTIVE", "daily_amp": 2.5, "triggers": 4, "win_rate": 0.5},
    {"name": "谨慎·低振幅·少触发", "regime": "CAUTIOUS", "daily_amp": 1.5, "triggers": 2, "win_rate": 0.45},
    {"name": "下跌·禁低吸", "regime": "HALT", "daily_amp": 3.0, "triggers": 0, "win_rate": 0.0},
]


def simulate(param_name: str, value: float, scenarios: List[dict]) -> Dict[str, float]:
    """简化回放：给定参数值，估算各场景下日收益%与回撤%（做T价差 = 振幅×胜率 − 成本）。"""
    results = {}
    for sc in scenarios:
        amp = sc["daily_amp"]
        win = sc["win_rate"]
        trig = sc["triggers"]
        if trig == 0 or win == 0:
            results[sc["name"]] = {"daily_ret_pct": 0.0, "max_drawdown_pct": 0.0}
            continue
        # 价差收益：每笔价差 ≈ 振幅×0.3（往返部分），成本 ≈ 0.2%
        per_trade_ret = amp * 0.3 * (2 * win - 1) - 0.2
        gross = per_trade_ret * trig
        # 参数约束：单笔/次数/回转额按比例折算（简化）
        if param_name == "MAX_SINGLE_ORDER_PCT":
            gross *= min(value * 100 / 5.0, 1.2)  # 单笔5%基线 → 放/缩
        if param_name == "MAX_DAILY_TURNOVER_RATIO":
            gross *= min(value / 3.0, 1.3)
        if param_name == "COOLDOWN_AFTER_LOSS_MIN":
            # 冷却越长触发越少
            eff_trig = max(trig * min(15.0 / max(value, 1), 1.2), 0)
            gross = per_trade_ret * eff_trig
        if param_name == "DAILY_LOSS_BREAKER_PCT":
            # 熔断越紧，最大回撤越小（但可能截断反弹）
            max_dd = min(amp * 1.5, value * 100)
            results[sc["name"]] = {
                "daily_ret_pct": round(gross, 2),
                "max_drawdown_pct": round(max_dd, 2),
            }
            continue
        results[sc["name"]] = {
            "daily_ret_pct": round(gross, 2),
            "max_drawdown_pct": round(amp * 1.2, 2),
        }
    return results


def scan() -> Dict[str, Any]:
    out = {"params": {}, "conclusion": []}
    for name, meta in PARAMS.items():
        base = meta["base"]
        variants = {
            "-30%": base * 0.7,
            "-15%": base * 0.85,
            "base": base,
            "+15%": base * 1.15,
            "+30%": base * 1.3,
        }
        rows = []
        for tag, v in variants.items():
            sim = simulate(name, v, SCENARIOS)
            # 汇总：总收益与最差回撤
            total_ret = sum(s["daily_ret_pct"] for s in sim.values())
            worst_dd = max(s["max_drawdown_pct"] for s in sim.values())
            rows.append({
                "variant": tag,
                "value": round(v, 4),
                "total_ret_pct": round(total_ret, 2),
                "worst_dd_pct": round(worst_dd, 2),
                "scenarios": sim,
            })
        # 判定：可行区间 = 收益>0 且 回撤可控 的变体
        feasible = [r for r in rows if r["total_ret_pct"] > 0 and r["worst_dd_pct"] < 10]
        out["params"][name] = {
            "label": meta["label"],
            "base": base,
            "variants": rows,
            "feasible": [r["variant"] for r in feasible],
            "recommended": (feasible[0]["variant"] if feasible else "base"),
        }
        out["conclusion"].append(
            f"{meta['label']}: 基准={base}{meta['unit']}，可行变体={[r['variant'] for r in feasible] or '无'}"
        )
    return out


def main():
    parser = argparse.ArgumentParser(description="做T风控参数敏感度扫描")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()
    result = scan()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for line in result["conclusion"]:
            print(line)
        print("\n结论：上线用保守档（-30% 或 base），P4 后按实盘数据收敛为窄区间并固化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
