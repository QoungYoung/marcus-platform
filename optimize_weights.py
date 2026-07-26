# -*- coding: utf-8 -*-
"""权重优化脚本：在落地数据上搜索最优 6 维权重组合，输出 CSV。

用法:
    python optimize_weights.py
    python optimize_weights.py --input data/scores_dump.csv --iterations 50000 --workers 4
"""

import argparse
import csv
import random
import sys
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

# ── 维度配置 ───────────────────────────────────────────────

DIMS = [
    "trend_score", "valuation_score", "reversal_score",
]

DIM_MAX = {
    "trend_score": 28, "valuation_score": 10, "reversal_score": 10,
}

DIM_CN = {
    "trend_score": "趋势综合", "valuation_score": "估值锚定", "reversal_score": "反转信号",
}

TARGETS = ["next_day_pct", "day3_pct", "day5_pct"]
REGIMES = ["trending", "ranging", "transitional"]


# ── 核心计算 ───────────────────────────────────────────────


def compute_composite(df: pd.DataFrame, weights: Dict[str, float]) -> pd.Series:
    score = pd.Series(0.0, index=df.index)
    for dim in DIMS:
        score += df[dim].fillna(0) * weights.get(dim, 0) * 100.0 / DIM_MAX[dim]
    return score


def evaluate_weights(df: pd.DataFrame, weights: Dict[str, float],
                     target: str = "day5_pct") -> float:
    """Spearman 秩相关（日内平均）。"""
    composite = compute_composite(df, weights)
    cors = []
    for _, grp in df.groupby("date"):
        idx = grp.index
        valid = pd.DataFrame({target: df.loc[idx, target], "_c": composite.loc[idx]}).dropna()
        if len(valid) >= 10:
            cor, _ = spearmanr(valid[target], valid["_c"])
            if not np.isnan(cor):
                cors.append(cor)
    return float(np.mean(cors)) if cors else 0.0


def random_weights(rng: random.Random) -> Dict[str, float]:
    raw = [rng.random() for _ in range(len(DIMS))]
    total = sum(raw)
    return dict(zip(DIMS, [r / total for r in raw]))


# ── 诊断 ───────────────────────────────────────────────────


def diagnosis(df: pd.DataFrame):
    """各维度 vs 前瞻收益 Spearman 相关矩阵。"""
    print("=" * 72)
    print("  各维度得分 vs 前瞻收益  Spearman 秩相关（日内平均）")
    print("=" * 72)
    header = f"{'维度':<14}"
    for t in TARGETS:
        header += f"  {t:>14}"
    print(header)

    for dim in DIMS:
        line = f"{DIM_CN[dim]:<14}"
        for t in TARGETS:
            cors = []
            for _, grp in df.groupby("date"):
                valid = grp[[dim, t]].dropna()
                if len(valid) >= 10:
                    cor, _ = spearmanr(valid[dim], valid[t])
                    if not np.isnan(cor):
                        cors.append(cor)
            avg = np.mean(cors) if cors else 0.0
            line += f"  {avg:>+14.4f}"
        print(line)

    # 默认权重基线（3维优化权重）
    default_w = {
        "trend_score": 0.25, "valuation_score": 0.57, "reversal_score": 0.18,
    }
    line = f"{'默认权重综合分':<14}"
    for t in TARGETS:
        line += f"  {evaluate_weights(df, default_w, t):>+14.4f}"
    print(line)

    eq_w = {d: 1.0 / len(DIMS) for d in DIMS}
    line = f"{'等价权重综合分':<14}"
    for t in TARGETS:
        line += f"  {evaluate_weights(df, eq_w, t):>+14.4f}"
    print(line)
    print()


# ── 搜索结果保存 ────────────────────────────────────────────


def save_results(results: List[Tuple[float, Dict[str, float]]],
                 output_path: Path, target: str, label: str = "all"):
    """保存搜索结果到 CSV。"""
    rows = []
    for rank, (cor, w) in enumerate(results, 1):
        row = {"rank": rank, "correlation": round(cor, 6), "target": target, "label": label}
        for dim in DIMS:
            row[dim] = round(w[dim], 6)
        rows.append(row)

    write_header = not output_path.exists()
    with open(output_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ── 搜索 ───────────────────────────────────────────────────


def search(df: pd.DataFrame, target: str = "day5_pct",
           iterations: int = 20000, seed: int = 42,
           workers: int = 8, csv_path: Optional[Path] = None,
           label: str = "all") -> List[Tuple[float, Dict[str, float]]]:
    """随机搜索 + 局部精炼。"""

    rng = random.Random(seed)
    weights_pool = [random_weights(rng) for _ in range(iterations)]
    results: List[Tuple[float, Dict[str, float]]] = []
    best_so_far = -1.0

    t_start = time.time()
    print(f"\n{'─' * 48}")
    print(f"  [{label}] 随机搜索 {iterations} 次  target={target}  workers={workers}")
    print(f"{'─' * 48}")

    def _eval(w):
        return (evaluate_weights(df, w, target), w)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_eval, w) for w in weights_pool]
        for i, future in enumerate(as_completed(futures)):
            cor, w = future.result()
            results.append((cor, w))

            if cor > best_so_far:
                best_so_far = cor
                elapsed = time.time() - t_start
                print(f"  [{i + 1}/{iterations}] 新最优 cor={cor:+.4f}  "
                      f"趋势={w['trend_score']:.3f} 估值={w['valuation_score']:.3f} 反转={w['reversal_score']:.3f}  "
                      f"耗时 {elapsed:.0f}s")

            elif (i + 1) % 2000 == 0:
                elapsed = time.time() - t_start
                print(f"  [{i + 1}/{iterations}] 当前最优 cor={best_so_far:+.4f}  耗时 {elapsed:.0f}s")

    # 排序
    results.sort(key=lambda x: x[0], reverse=True)
    top20 = results[:20]

    # 局部精炼 top 5
    print(f"\n  局部精炼 top 5 ...")
    refined = list(top20[:5])
    for _ in range(5):
        new_candidates = []
        for cor, w in refined:
            for _ in range(50):
                w2 = dict(w)
                d1, d2 = rng.sample(DIMS, 2)
                delta = rng.uniform(0.005, 0.03)
                if w2[d1] >= delta and w2[d2] + delta <= 1.0:
                    w2[d1] -= delta
                    w2[d2] += delta
                total = sum(w2.values())
                w2 = {k: v / total for k, v in w2.items()}
                new_cor = evaluate_weights(df, w2, target)
                new_candidates.append((new_cor, w2))
        refined.extend(new_candidates)
        refined.sort(key=lambda x: x[0], reverse=True)
        refined = refined[:20]

    # 打印结果
    final = refined
    print(f"\n  [{label}] Top 10 权重:")
    header = f"  {'Rank':<5} {'Cor':>8} "
    for dim in DIMS:
        header += f" {DIM_CN[dim]:<8}"
    print(header)
    for rank, (cor, w) in enumerate(final[:10], 1):
        line = f"  {rank:<5} {cor:>+8.4f} "
        for dim in DIMS:
            line += f" {w[dim]:<8.3f}"
        print(line)

    # 保存 CSV
    if csv_path:
        save_results(final, csv_path, target, label)
        print(f"  -> 已保存至 {csv_path}")

    return final


# ── 主入口 ─────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="行业龙头排行权重优化")
    parser.add_argument("--input", type=str, default="data/scores_dump.csv")
    parser.add_argument("--target", type=str, default="day5_pct", choices=TARGETS)
    parser.add_argument("--iterations", type=int, default=30000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--no-regime", action="store_true",
                        help="不区分市场状态")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    # 读数据
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] 找不到 {input_path}")
        sys.exit(1)

    df = pd.read_csv(input_path, encoding="utf-8-sig")
    df = df.dropna(subset=[args.target])
    print(f"[DATA] {len(df)} 行  {df['date'].nunique()} 个交易日  "
          f"{df['symbol'].nunique()} 只股票  target={args.target}")

    # CSV 输出路径
    if args.output:
        csv_path = Path(args.output)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path = Path(f"data/weights_result_{ts}.csv")
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    # 清空旧 CSV
    if csv_path.exists():
        csv_path.unlink()

    # 1. 诊断
    diagnosis(df)

    # 2. 全市场搜索
    all_best = search(df, args.target, iterations=args.iterations,
                      workers=args.workers, seed=args.seed,
                      csv_path=csv_path, label="all")

    # 3. 等价权重对比
    eq_cor = evaluate_weights(df, {d: 1.0 / len(DIMS) for d in DIMS}, args.target)
    print(f"\n  等价权重: cor={eq_cor:+.4f}  |  最优: cor={all_best[0][0]:+.4f}  "
          f"|  提升: {all_best[0][0] - eq_cor:+.4f}")

    # 4. 按 regime 搜索
    if not args.no_regime:
        for regime in REGIMES:
            sub = df[df["market_regime"] == regime]
            if len(sub) < 100 or sub["date"].nunique() < 3:
                print(f"\n  [{regime}] 数据不足，跳过 ({len(sub)} 行)")
                continue
            search(sub, args.target,
                   iterations=max(args.iterations // 3, 5000),
                   workers=args.workers, seed=args.seed,
                   csv_path=csv_path, label=regime)

    print(f"\n{'=' * 72}")
    print(f"  结果已保存至: {csv_path}")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    main()
