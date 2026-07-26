# -*- coding: utf-8 -*-
"""验证线性加权假设：XGBoost vs 线性模型 vs 等价权重，走步前进对比。

用法:
    python scripts/compare_linear_vs_xgb.py
    python scripts/compare_linear_vs_xgb.py --input data/scores_dump.csv --target day5_pct --train-days 40
"""

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from xgboost import XGBRegressor

# ── 维度配置 ───────────────────────────────────────────────

DIMS = [
    "trend_score", "valuation_score", "reversal_score",
]

DIM_MAX = {
    "trend_score": 28, "valuation_score": 10, "reversal_score": 10,
}

DIM_CN = {
    "trend_score": "Trend", "valuation_score": "Valuation", "reversal_score": "Reversal",
}


def normalize_scores(df: pd.DataFrame) -> pd.DataFrame:
    """将原始得分除以满分，映射到 [0,1] 区间。"""
    out = df.copy()
    for dim in DIMS:
        out[dim] = out[dim].fillna(0) / DIM_MAX[dim]
    return out


def linear_score(X: pd.DataFrame, weights: Dict[str, float]) -> np.ndarray:
    """线性加权综合分。"""
    s = np.zeros(len(X))
    for dim in DIMS:
        s += X[dim].values * weights[dim]
    return s


def daily_ic(df: pd.DataFrame, pred_col: str, target: str) -> float:
    """按日计算 Spearman 秩相关，取均值。"""
    cors = []
    for _, grp in df.groupby("date"):
        valid = grp[[pred_col, target]].dropna()
        if len(valid) >= 10:
            cor, _ = spearmanr(valid[pred_col], valid[target])
            if not np.isnan(cor):
                cors.append(cor)
    return float(np.mean(cors)) if cors else 0.0


def walk_forward(
    df: pd.DataFrame,
    target: str,
    train_days: int,
    step: int = 5,
) -> List[Dict]:
    """走步前进验证，返回每步的 daily IC 结果。"""

    dates = sorted(df["date"].unique())
    results = []

    for test_start in range(train_days, len(dates), step):
        test_end = min(test_start + step, len(dates))
        train_dates = dates[:test_start]
        test_dates = dates[test_start:test_end]

        train = df[df["date"].isin(train_dates)].copy()
        test = df[df["date"].isin(test_dates)].copy()

        if len(test) < 20 or test["date"].nunique() < 2:
            continue

        # ── 准备特征 ──
        X_train = train[DIMS].copy()
        y_train = train[target].values
        X_test = test[DIMS].copy()
        y_test = test[target].values

        # 归一化到 [0,1]
        X_train_n = normalize_scores(X_train)[DIMS].values
        X_test_n = normalize_scores(X_test)[DIMS].values

        # ── 1. 等价权重 ──
        eq_weights = {d: 1.0 / len(DIMS) for d in DIMS}
        test["pred_eq"] = linear_score(normalize_scores(X_test), eq_weights)

        # ── 2. Ridge 线性模型 ──
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train_n)
        X_test_s = scaler.transform(X_test_n)

        ridge = Ridge(alpha=1.0)
        ridge.fit(X_train_s, y_train)
        test["pred_ridge"] = ridge.predict(X_test_s)

        # ── 3. XGBoost 非线性模型 ──
        xgb = XGBRegressor(
            n_estimators=100, max_depth=3, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
            random_state=42, verbosity=0,
        )
        xgb.fit(X_train_n, y_train)
        test["pred_xgb"] = xgb.predict(X_test_n)

        # ── 计算各模型 IC ──
        ic_eq = daily_ic(test, "pred_eq", target)
        ic_ridge = daily_ic(test, "pred_ridge", target)
        ic_xgb = daily_ic(test, "pred_xgb", target)

        results.append({
            "test_dates": f"{test_dates[0]} ~ {test_dates[-1]}",
            "n_train": len(train), "n_test": len(test),
            "IC_eq": ic_eq, "IC_ridge": ic_ridge, "IC_xgb": ic_xgb,
        })

        print(
            f"  {test_dates[0]} ~ {test_dates[-1]}  "
            f"train={len(train):>4}  test={len(test):>3}  "
            f"IC_eq={ic_eq:+.4f}  IC_ridge={ic_ridge:+.4f}  IC_xgb={ic_xgb:+.4f}"
        )

    return results


def main():
    parser = argparse.ArgumentParser(description="线性 vs XGBoost 走步前进对比")
    parser.add_argument("--input", type=str, default="data/scores_dump.csv")
    parser.add_argument("--target", type=str, default="day5_pct")
    parser.add_argument("--train-days", type=int, default=40,
                        help="初始训练天数，之后每 step 天测试一次")
    parser.add_argument("--step", type=int, default=5,
                        help="每轮向前滚动天数")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[ERROR] 找不到 {input_path}")
        sys.exit(1)

    df = pd.read_csv(input_path, encoding="utf-8-sig")
    df = df.dropna(subset=DIMS + [args.target])

    print(f"[DATA] {len(df)} 行  {df['date'].nunique()} 交易日  "
          f"{df['symbol'].nunique()} 只股票  target={args.target}")
    print(f"[DATA] 日期范围: {df['date'].min()} ~ {df['date'].max()}")
    print()

    # 基线：全样本单日 IC（每个维度 vs target）
    print("=" * 72)
    print("  各维度 vs target 全样本 Spearman IC（仅参考，非样本外）")
    print("=" * 72)
    for dim in DIMS:
        ic = daily_ic(df.assign(pred=df[dim]), "pred", args.target)
        bar = "+" * max(1, int(ic * 200)) if ic > 0 else "-" * max(1, int(-ic * 200))
        print(f"  {DIM_CN[dim]:<10}  IC={ic:+.4f}  {bar}")
    print()

    print("=" * 72)
    print(f"  走步前进验证 (train={args.train_days}天, step={args.step}天)")
    print("=" * 72)
    print()

    results = walk_forward(df, args.target, args.train_days, args.step)

    # ── 汇总 ──
    if not results:
        print("\n[ERROR] 没有足够的测试窗口，尝试减少 --train-days")
        sys.exit(1)

    print()
    print("=" * 72)
    print("  汇总：各模型走步前进 IC（样本外）")
    print("=" * 72)

    ic_eq_list = [r["IC_eq"] for r in results]
    ic_ridge_list = [r["IC_ridge"] for r in results]
    ic_xgb_list = [r["IC_xgb"] for r in results]

    def _summary(label, vals):
        mean_v = np.mean(vals)
        std_v = np.std(vals, ddof=1)
        win_rate = sum(1 for v in vals if v > 0) / len(vals)
        print(f"  {label:<10}  mean={mean_v:+.4f}  std={std_v:.4f}  "
              f"IC>0比例={win_rate:.0%}")

    _summary("等价权重", ic_eq_list)
    _summary("Ridge线性", ic_ridge_list)
    _summary("XGBoost", ic_xgb_list)

    # ── 显著性检验 ──
    from scipy.stats import wilcoxon
    print()
    print("  配对 Wilcoxon 检验 (Ridge vs XGB):")
    try:
        stat, p = wilcoxon(ic_ridge_list, ic_xgb_list, zero_method="zsplit")
        print(f"    statistic={stat:.4f}  p={p:.4f}  "
              f"{'显著差异' if p < 0.05 else '无显著差异'}")
    except ValueError:
        print("    (样本太少，无法计算)")

    # ── 结论 ──
    print()
    print("=" * 72)
    mean_xgb = np.mean(ic_xgb_list)
    mean_ridge = np.mean(ic_ridge_list)
    mean_eq = np.mean(ic_eq_list)

    if mean_xgb > mean_ridge + 0.01:
        print("  结论: XGBoost 显著优于线性模型 → 线性假设不成立，建议切换到 ML")
    elif mean_ridge > mean_xgb + 0.01:
        print("  结论: Ridge 线性模型更优 → 线性假设成立，继续用加权方式")
    elif mean_ridge > mean_eq + 0.005:
        print("  结论: 最优权重 vs XGBoost 差异不大 → 线性假设基本成立")
    else:
        print("  结论: 所有模型差异不大 → 6 维得分对收益的预测能力有限")
    print("=" * 72)


if __name__ == "__main__":
    main()
