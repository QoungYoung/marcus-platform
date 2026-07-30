# -*- coding: utf-8 -*-
"""方向预测服务 v3：纯截面 XGBoost 回归预测行业龙头持有期收益。

v3 关键变更（三轮专家评审通过）:
  - 26 维纯截面特征（18 个股 + 7 行业），移除 MARKET_COLS/INDEX_COLS
  - Pseudo-Huber 损失 (reg:pseudohubererror) 鲁棒处理厚尾
  - 走步前进验证 + 窗口敏感性测试 (60/120/180/250)
  - 纯多头评估：IR vs CSI 300, 月胜率, Calmar, 最大回撤
  - 无市场状态分类器、无校准器、无 regime 乘数
"""

import logging
import pickle
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 特征配置（26 维：18 个股 + 7 行业）────────────────────────

FEATURE_COLS = [
    # 量价结构
    "vol_ratio_5d", "vol_ratio_1d", "amount_breakout",
    "up_vol_ratio",
    # 趋势强度
    "consecutive_up", "consecutive_down",
    "gap_up_pct",
    # 日行情
    "change_pct", "turnover_rate",
    # 资金流向
    "big_order_net", "main_force_ratio", "flow_5d_cum",
    # 动量
    "ret_5d", "ret_10d",
    # 技术指标
    "rsi6", "rsi14", "ma20_deviation",
    # 交互特征
    "strength_score",
]

SECTOR_COLS = [
    "sector_pct",         # change_pct - 行业中位数
    "sector_ret_5d",      # ret_5d - 行业中位数
    "sector_vol",         # vol_ratio_1d - 行业中位数
    "sector_mf",          # big_order_net - 行业中位数
    "sector_money_flow",  # 行业资金流向总和
    "sector_breadth",     # 行业上涨占比
    "sector_rank",        # 行业在全市场中涨跌幅排名
]

ALL_FEATURES = FEATURE_COLS + SECTOR_COLS
HORIZONS = ["1d", "3d", "5d"]
TRANSACTION_COST = 0.0018  # 0.18% 往返交易成本


def _get_data_dir() -> Path:
    try:
        from app.config import get_settings
        return get_settings().data_dir
    except Exception:
        pass
    candidates = [Path(__file__).resolve().parents[3] / "data", Path.cwd() / "data"]
    for c in candidates:
        if c.exists():
            return c
    return Path.cwd() / "data"


# ── 特征推导（T 日数据，与 dump_direction_data.py 保持一致）────

def _rsi_from_closes(closes: List[float], period: int) -> float:
    """从收盘价序列计算 RSI。"""
    n = min(period, len(closes) - 1)
    if n <= 0:
        return 50.0
    diffs = [closes[i] - closes[i - 1] for i in range(-n, 0)]
    avg_gain = sum(max(d, 0) for d in diffs) / n
    avg_loss = sum(max(-d, 0) for d in diffs) / n
    return round(100 - 100 / (1 + avg_gain / avg_loss), 1) if avg_loss > 0 else 100.0


def derive_stock_features(daily_bars: List[dict], quote: dict,
                          moneyflow: Optional[dict] = None) -> dict:
    """从日线和资金流向数据推导单只股票的特征。

    特征基于 T 日收盘后数据（daily_bars 最后一天是 T 日）。
    与 dump_direction_data.py 的 derive_features_from_bars 逻辑一致。
    """
    f: dict = {}

    if len(daily_bars) >= 5:
        closes = [float(b["close"]) for b in daily_bars]
        volumes = [float(b.get("vol", 0) or 0) for b in daily_bars]
        amounts = [float(b.get("amount", 0) or 0) for b in daily_bars]

        # --- 成交量突破 ---
        if len(daily_bars) >= 20:
            avg_vol_20 = sum(volumes[-20:]) / 20
            avg_vol_5 = sum(volumes[-5:]) / 5
            f["vol_ratio_5d"] = round(avg_vol_5 / avg_vol_20, 3) if avg_vol_20 > 0 else 1.0
            f["vol_ratio_1d"] = round(volumes[-1] / avg_vol_20, 3) if avg_vol_20 > 0 else 1.0
            median_amount = sorted(amounts[-20:])[len(amounts[-20:]) // 2]
            f["amount_breakout"] = 1 if (median_amount > 0 and amounts[-1] > 2 * median_amount) else 0
        else:
            avg_vol = sum(volumes) / len(volumes)
            f["vol_ratio_5d"] = 1.0
            f["vol_ratio_1d"] = round(volumes[-1] / avg_vol, 3) if avg_vol > 0 else 1.0
            f["amount_breakout"] = 0

        # --- 上涨/下跌日成交量比 ---
        if len(daily_bars) >= 6:
            up_vols, down_vols = [], []
            for i in range(-5, 0):
                if closes[i] > closes[i - 1]:
                    up_vols.append(volumes[i])
                elif closes[i] < closes[i - 1]:
                    down_vols.append(volumes[i])
            up_sum = sum(up_vols)
            down_sum = sum(down_vols)
            f["up_vol_ratio"] = round(up_sum / down_sum, 3) if down_sum > 0 else (2.0 if up_sum > 0 else 1.0)
        else:
            f["up_vol_ratio"] = 1.0

        # --- 连涨连跌 ---
        cons_up, cons_down = 0, 0
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] > closes[i - 1]:
                cons_up += 1
            else:
                break
        for i in range(len(closes) - 1, 0, -1):
            if closes[i] < closes[i - 1]:
                cons_down += 1
            else:
                break
        f["consecutive_up"] = cons_up
        f["consecutive_down"] = cons_down

        # --- 缺口检测 ---
        gap_up_pct = 0.0
        if len(daily_bars) >= 2:
            prev_close = float(daily_bars[-2]["close"])
            today_open = float(daily_bars[-1].get("open", closes[-1]))
            if prev_close > 0:
                gap = (today_open - prev_close) / prev_close * 100
                if gap > 0.5:
                    gap_up_pct = gap
        f["gap_up_pct"] = round(gap_up_pct, 2)

        # --- 多日动量 ---
        if len(closes) >= 6:
            f["ret_5d"] = round((closes[-1] / closes[-6] - 1) * 100, 2)
        else:
            f["ret_5d"] = 0.0
        if len(closes) >= 11:
            f["ret_10d"] = round((closes[-1] / closes[-11] - 1) * 100, 2)
        else:
            f["ret_10d"] = 0.0

        # --- RSI ---
        f["rsi6"] = _rsi_from_closes(closes, 6)
        f["rsi14"] = _rsi_from_closes(closes, 14)

        # --- MA20 偏离 ---
        lookback20 = min(20, len(closes))
        ma20 = sum(closes[-lookback20:]) / lookback20
        f["ma20_deviation"] = round((closes[-1] - ma20) / ma20 * 100, 2) if ma20 > 0 else 0

    else:
        f.update({
            "vol_ratio_5d": 1.0, "vol_ratio_1d": 1.0, "amount_breakout": 0,
            "up_vol_ratio": 1.0,
            "consecutive_up": 0, "consecutive_down": 0,
            "gap_up_pct": 0,
            "ret_5d": 0, "ret_10d": 0,
            "rsi6": 50.0, "rsi14": 50.0, "ma20_deviation": 0,
            "strength_score": 0,
        })

    # --- T 日行情 ---
    f["change_pct"] = float(quote.get("change_pct", 0) or 0)
    f["turnover_rate"] = float(quote.get("turnover_rate", 0) or 0)

    # --- 资金流向（万元 → 亿元，与 dump 保持一致）---
    if moneyflow and moneyflow.get("available"):
        f["big_order_net"] = round(float(moneyflow.get("big_order_net", 0)) / 1e4, 2)
        f["main_force_ratio"] = round(float(moneyflow.get("main_force_ratio", 0)), 3)
        f["flow_5d_cum"] = round(float(moneyflow.get("flow_5d_cum", 0)) / 1e4, 2)
    else:
        f["big_order_net"] = 0.0
        f["main_force_ratio"] = 0.0
        f["flow_5d_cum"] = 0.0

    return f


def add_sector_features(features_data: Dict[str, dict],
                        industries: Dict[str, str]) -> Dict[str, dict]:
    """为所有股票添加行业内部相对强度 + 行业整体特征。

    行业统计基于全市场活跃股票计算，确保统计意义。
    与 dump_direction_data.py 的 _compute_sector_features 逻辑一致。
    """
    if not features_data:
        return features_data

    ind_map: Dict[str, List[str]] = {}
    for sym, ind in industries.items():
        if sym in features_data:
            ind_map.setdefault(ind, []).append(sym)

    syms = list(features_data.keys())
    pcts = np.array([features_data[s].get("change_pct", 0) for s in syms], dtype=float)
    valid_mask = ~np.isnan(pcts) & ~np.isinf(pcts)
    pct_ranks = np.full(len(syms), 0.5, dtype=float)
    if valid_mask.sum() >= 2:
        from scipy.stats import rankdata
        valid_vals = pcts[valid_mask]
        pct_ranks[valid_mask] = (rankdata(valid_vals) - 1) / (valid_mask.sum() - 1)

    # 行业中位数涨跌幅 → 行业排名
    ind_med_pcts: Dict[str, float] = {}
    for ind, grp in ind_map.items():
        grp_pcts = sorted(features_data[s].get("change_pct", 0) for s in grp)
        ind_med_pcts[ind] = grp_pcts[len(grp_pcts) // 2]

    ind_ranks: Dict[str, float] = {}
    if len(ind_med_pcts) >= 2:
        ind_names = list(ind_med_pcts.keys())
        ind_vals = np.array([ind_med_pcts[n] for n in ind_names], dtype=float)
        ind_rank_vals = (rankdata(ind_vals) - 1) / (len(ind_vals) - 1)
        ind_ranks = {n: round(float(r), 3) for n, r in zip(ind_names, ind_rank_vals)}
    else:
        ind_ranks = {n: 0.5 for n in ind_med_pcts}

    for ind, grp in ind_map.items():
        if len(grp) < 2:
            for sym in grp:
                features_data[sym].update({
                    "sector_pct": 0.0, "sector_ret_5d": 0.0,
                    "sector_vol": 0.0, "sector_mf": 0.0,
                    "sector_money_flow": 0.0, "sector_breadth": 0.5, "sector_rank": 0.5,
                })
            continue

        def _median(field):
            vals = sorted(features_data[s].get(field, 0) for s in grp)
            return vals[len(vals) // 2]

        med_pct = _median("change_pct")
        med_ret = _median("ret_5d")
        med_vol = _median("vol_ratio_1d")
        med_mf = _median("big_order_net")

        total_mf = sum(features_data[s].get("big_order_net", 0) for s in grp)
        up_count = sum(1 for s in grp if features_data[s].get("change_pct", 0) > 0)
        breadth = round(up_count / len(grp), 3)

        for i, sym in enumerate(grp):
            f = features_data[sym]
            f["sector_pct"] = round(f.get("change_pct", 0) - med_pct, 2)
            f["sector_ret_5d"] = round(f.get("ret_5d", 0) - med_ret, 2)
            f["sector_vol"] = round(f.get("vol_ratio_1d", 1.0) - med_vol, 3)
            f["sector_mf"] = round(f.get("big_order_net", 0) - med_mf, 2)
            f["sector_money_flow"] = round(total_mf, 2)
            f["sector_breadth"] = breadth
            f["sector_rank"] = ind_ranks.get(ind, 0.5)
            f["strength_score"] = round(pct * pct_ranks[i], 2)

    return features_data


def compute_market_breadth(quotes: Dict[str, dict]) -> dict:
    """从当日行情截面计算市场宽度特征（用于市场状态检测，非模型输入）。"""
    pcts = [float(q.get("change_pct", 0) or 0) for q in quotes.values()]
    amounts = [float(q.get("amount", 0) or 0) for q in quotes.values() if q.get("amount", 0)]
    n = len(pcts)
    if n == 0:
        return {}

    up_count = sum(1 for p in pcts if p > 0)
    limit_up_count = sum(1 for p in pcts if p >= 9.5)
    down_count = sum(1 for p in pcts if p < 0)

    return {
        "up_ratio": round(up_count / n, 3),
        "limit_up_count": limit_up_count,
        "advance_decline": round(up_count / max(down_count, 1), 3),
        "mkt_mean": round(sum(pcts) / n, 2),
        "mkt_vol": round(float(np.std(pcts, ddof=1)), 2) if len(pcts) > 1 else 0,
        "mkt_amount_total": sum(amounts),
    }


def fetch_index_features(pro, end_date: str,
                         index_code: str = "000001.SH") -> dict:
    """获取指数衍生特征（上证指数，用于市场状态检测，非模型输入）。

    Returns:
        {index_ret_5d, index_rsi14, index_ma20_deviation}
    """
    defaults = {"index_ret_5d": 0.0, "index_rsi14": 50.0, "index_ma20_deviation": 0.0}

    try:
        start_dt = datetime.strptime(end_date, "%Y%m%d") - timedelta(days=50)
        start_date = start_dt.strftime("%Y%m%d")

        df = None
        try:
            df = pro.index_daily(ts_code=index_code, start_date=start_date,
                                 end_date=end_date)
        except Exception:
            pass

        if df is None or df.empty:
            try:
                df = pro.daily(ts_code=index_code, start_date=start_date,
                               end_date=end_date)
            except Exception:
                return defaults

        if df is None or df.empty:
            return defaults

        df = df.sort_values("trade_date")
        closes = [float(r.get("close", 0) or 0) for _, r in df.iterrows()]

        if len(closes) < 6:
            return defaults

        ret_5d = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if closes[-6] > 0 else 0

        period = min(14, len(closes) - 1)
        if period > 0:
            diffs = [closes[i] - closes[i - 1] for i in range(-period, 0)]
            avg_gain = sum(max(d, 0) for d in diffs) / period
            avg_loss = sum(max(-d, 0) for d in diffs) / period
            rsi14 = round(100 - 100 / (1 + avg_gain / avg_loss), 1) if avg_loss > 0 else 100.0
        else:
            rsi14 = 50.0

        lookback = min(20, len(closes))
        ma20 = sum(closes[-lookback:]) / lookback
        ma20_dev = round((closes[-1] - ma20) / ma20 * 100, 2) if ma20 > 0 else 0

        return {
            "index_ret_5d": ret_5d,
            "index_rsi14": rsi14,
            "index_ma20_deviation": ma20_dev,
        }
    except Exception:
        return defaults


# ── 方向预测服务 ───────────────────────────────────────────


class DirectionPredictionService:
    """个股预期收益率预测（XGBoost 回归 + Pseudo-Huber loss）。

    三个独立模型（1d/3d/5d），每个使用 26 维纯截面特征。
    支持走步前进训练、Optuna 超参搜索、窗口敏感性测试。
    """

    def __init__(self):
        self.models: Dict[str, object] = {}    # horizon → trained XGBRegressor
        self.scalers: Dict[str, object] = {}   # horizon → StandardScaler
        self.metrics: Dict[str, dict] = {}     # horizon → validation metrics
        self.portfolio_metrics: Dict[str, dict] = {}  # horizon → long-only portfolio metrics

    # ── 训练 ─────────────────────────────────────────────

    @staticmethod
    def _tune_xgb(X, y, n_trials: int = 50, random_state: int = 42) -> dict:
        """用 Optuna 搜索 XGBRegressor 超参，最小化 Pseudo-Huber loss。

        Pseudo-Huber 对厚尾收益分布鲁棒：小残差二次惩罚，大残差线性惩罚。
        """
        import optuna
        from xgboost import XGBRegressor
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import mean_squared_error

        tscv = TimeSeriesSplit(n_splits=3)

        def objective(trial):
            params = {
                "max_depth": trial.suggest_int("max_depth", 4, 10),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
                "n_estimators": trial.suggest_int("n_estimators", 100, 500),
                "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-6, 1.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-6, 1.0, log=True),
                "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
                "objective": "reg:pseudohubererror",
                "random_state": random_state,
                "verbosity": 0,
            }

            rmses = []
            for train_idx, val_idx in tscv.split(X):
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]

                model = XGBRegressor(**params)
                model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

                y_pred = model.predict(X_val)
                rmse = np.sqrt(mean_squared_error(y_val, y_pred))
                rmses.append(rmse)

                trial.report(-rmse, step=len(rmses) - 1)
                if trial.should_prune():
                    raise optuna.TrialPruned()

            return float(np.mean(rmses))

        sampler = optuna.samplers.TPESampler(seed=random_state)
        study = optuna.create_study(
            direction="minimize", sampler=sampler,
            pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=n_trials > 20)

        return study.best_params

    def train_walk_forward(self, data_path: str, train_days: int = 120,
                           step: int = 10, n_trials: int = 50,
                           window_sizes: Optional[List[int]] = None,
                           verbose: bool = True):
        """走步前进训练 + 验证（滚动窗口）。

        评估指标：
          - RMSE, Spearman R, R²: 回归质量
          - 方向准确率: 辅助指标

        Args:
            data_path: dump_direction_data.py 输出的 CSV 路径
            train_days: 每个窗口的训练天数
            step: 每轮向前滚动天数
            n_trials: Optuna 超参搜索试验次数
            window_sizes: 窗口敏感性测试的窗口大小列表，默认 [60, 120, 180, 250]
        """
        from sklearn.preprocessing import StandardScaler
        from xgboost import XGBRegressor

        if window_sizes is None:
            window_sizes = [60, 120, 180, 250]

        df = pd.read_csv(data_path, encoding="utf-8-sig")
        dates = sorted(df["date"].unique())

        if len(dates) < train_days + step:
            raise ValueError(f"数据不足：{len(dates)} 天，需要至少 {train_days + step} 天")

        all_results = {h: [] for h in HORIZONS}
        tuned_params = {h: None for h in HORIZONS}

        # 组合跟踪（纯多头，非重叠入场）
        portfolio_nav = {h: 1.0 for h in HORIZONS}
        portfolio_peak = {h: 1.0 for h in HORIZONS}
        portfolio_returns = {h: [] for h in HORIZONS}
        portfolio_max_dd = {h: 0.0 for h in HORIZONS}
        hold_days = {"1d": 1, "3d": 3, "5d": 5}

        is_first_window = True

        for test_start in range(train_days, len(dates), step):
            test_end = min(test_start + step, len(dates))
            train_dates = dates[test_start - train_days:test_start]
            test_dates = dates[test_start:test_end]

            train = df[df["date"].isin(train_dates)].copy()
            test = df[df["date"].isin(test_dates)].copy()

            for horizon, target_col in zip(HORIZONS, ["target_1d", "target_3d", "target_5d"]):
                train_h = train.dropna(subset=ALL_FEATURES + [target_col])
                test_h = test.dropna(subset=ALL_FEATURES + [target_col])

                if len(train_h) < 100 or len(test_h) < 50:
                    continue

                X_train = train_h[ALL_FEATURES].values
                y_train = train_h[target_col].values.astype(float)
                X_test = test_h[ALL_FEATURES].values
                y_test = test_h[target_col].values.astype(float)

                scaler = StandardScaler()
                X_train_s = scaler.fit_transform(X_train)
                X_test_s = scaler.transform(X_test)

                if is_first_window and n_trials > 0 and tuned_params[horizon] is None:
                    if verbose:
                        print(f"  [{horizon}] Optuna tuning ({n_trials} trials)...")
                    try:
                        tuned_params[horizon] = self._tune_xgb(
                            X_train_s, y_train, n_trials=n_trials)
                        if verbose:
                            print(f"  [{horizon}] Best params: {tuned_params[horizon]}")
                    except Exception as e:
                        if verbose:
                            print(f"  [{horizon}] Tuning failed ({e}), using defaults")
                        tuned_params[horizon] = {}

                best = tuned_params.get(horizon) or {}
                model = XGBRegressor(
                    n_estimators=best.get("n_estimators", 200),
                    max_depth=best.get("max_depth", 5),
                    learning_rate=best.get("learning_rate", 0.05),
                    subsample=best.get("subsample", 0.8),
                    colsample_bytree=best.get("colsample_bytree", 0.8),
                    reg_alpha=best.get("reg_alpha", 0.1),
                    reg_lambda=best.get("reg_lambda", 1.0),
                    min_child_weight=best.get("min_child_weight", 1),
                    objective="reg:pseudohubererror",
                    random_state=42, verbosity=0,
                )
                model.fit(X_train_s, y_train)

                y_pred = model.predict(X_test_s)

                # 回归指标
                rmse = float(np.sqrt(np.mean((y_pred - y_test) ** 2)))
                from scipy.stats import spearmanr
                spear, _ = spearmanr(y_pred, y_test)
                spear = float(spear)
                ss_res = float(np.sum((y_test - y_pred) ** 2))
                ss_tot = float(np.sum((y_test - y_test.mean()) ** 2))
                r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

                # 方向准确率
                dir_acc = float(np.mean((y_pred > 0) == (y_test > 0)))

                all_results[horizon].append({
                    "window": f"{test_dates[0]}~{test_dates[-1]}",
                    "n_train": len(train_h), "n_test": len(test_h),
                    "rmse": rmse, "spearman_r": spear, "r2": round(r2, 4),
                    "direction_accuracy": dir_acc,
                    "y_mean": float(y_test.mean()),
                })

                # 保存最后一个窗口的模型
                self.models[horizon] = model
                self.scalers[horizon] = scaler

                if verbose:
                    print(f"  {test_dates[0]}~{test_dates[-1]} {horizon}: "
                          f"RMSE={rmse:.3f} SpearmanR={spear:.3f} DirAcc={dir_acc:.1%}")

            is_first_window = False

        # 汇总指标
        for horizon in HORIZONS:
            if all_results[horizon]:
                rmses = [r["rmse"] for r in all_results[horizon]]
                spears = [r["spearman_r"] for r in all_results[horizon]]
                dir_accs = [r["direction_accuracy"] for r in all_results[horizon]]
                self.metrics[horizon] = {
                    "rmse_mean": float(np.mean(rmses)),
                    "spearman_r_mean": float(np.mean(spears)),
                    "direction_accuracy_mean": float(np.mean(dir_accs)),
                    "n_windows": len(rmses),
                }

        # 窗口敏感性测试
        if verbose and len(window_sizes) > 1:
            print("\n── 窗口敏感性测试 ──")
            sensitivity = self._run_sensitivity_test(df, window_sizes, step, verbose)
            for horizon in HORIZONS:
                if horizon in sensitivity:
                    for ws, m in sensitivity[horizon].items():
                        print(f"  {horizon} window={ws}d: "
                              f"SpearmanR={m['spearman_r_mean']:.3f} "
                              f"RMSE={m['rmse_mean']:.3f}")

        return all_results

    def _run_sensitivity_test(self, df: pd.DataFrame,
                              window_sizes: List[int], step: int,
                              verbose: bool) -> Dict[str, Dict[int, dict]]:
        """测试不同训练窗口长度的模型敏感性。"""
        from sklearn.preprocessing import StandardScaler
        from xgboost import XGBRegressor

        dates = sorted(df["date"].unique())
        results: Dict[str, Dict[int, dict]] = {h: {} for h in HORIZONS}

        for window_size in window_sizes:
            if len(dates) < window_size + step:
                continue
            if verbose:
                print(f"  测试窗口={window_size}d ...")

            wf_results = {h: [] for h in HORIZONS}
            for test_start in range(window_size, len(dates), step):
                test_end = min(test_start + step, len(dates))
                train_dates = dates[test_start - window_size:test_start]
                test_dates = dates[test_start:test_end]

                train = df[df["date"].isin(train_dates)].copy()
                test = df[df["date"].isin(test_dates)].copy()

                for horizon, target_col in zip(HORIZONS, ["target_1d", "target_3d", "target_5d"]):
                    train_h = train.dropna(subset=ALL_FEATURES + [target_col])
                    test_h = test.dropna(subset=ALL_FEATURES + [target_col])
                    if len(train_h) < 100 or len(test_h) < 50:
                        continue

                    X_train = train_h[ALL_FEATURES].values
                    y_train = train_h[target_col].values.astype(float)
                    X_test = test_h[ALL_FEATURES].values
                    y_test = test_h[target_col].values.astype(float)

                    scaler = StandardScaler()
                    X_train_s = scaler.fit_transform(X_train)
                    X_test_s = scaler.transform(X_test)

                    model = XGBRegressor(
                        n_estimators=200, max_depth=5, learning_rate=0.05,
                        subsample=0.8, colsample_bytree=0.8,
                        objective="reg:pseudohubererror",
                        random_state=42, verbosity=0,
                    )
                    model.fit(X_train_s, y_train)
                    y_pred = model.predict(X_test_s)

                    rmse = float(np.sqrt(np.mean((y_pred - y_test) ** 2)))
                    from scipy.stats import spearmanr
                    spear, _ = spearmanr(y_pred, y_test)
                    wf_results[horizon].append({"rmse": rmse, "spearman_r": float(spear)})

            for horizon in HORIZONS:
                if wf_results[horizon]:
                    rmses = [r["rmse"] for r in wf_results[horizon]]
                    spears = [r["spearman_r"] for r in wf_results[horizon]]
                    results[horizon][window_size] = {
                        "rmse_mean": float(np.mean(rmses)),
                        "spearman_r_mean": float(np.mean(spears)),
                        "n_windows": len(rmses),
                    }

        return results

    def evaluate_long_only(self, data_path: str, horizon: str = "5d",
                           top_n: int = 10, benchmark_col: str = "000001.SH",
                           train_days: int = 120, step: int = 10,
                           verbose: bool = True) -> Dict[str, float]:
        """纯多头组合评估：走步前进 OOS 模拟。

        在每个走步前进测试窗口中，对每个测试日：
        1. 用训练窗口数据训练的模型预测
        2. 选择 Top-N 预测股票
        3. 以非重叠入场（每 N 天一次）记录实际收益
        4. 跨窗口连续跟踪 NAV

        这是真正的样本外评估。
        """
        from sklearn.preprocessing import StandardScaler
        from xgboost import XGBRegressor

        df = pd.read_csv(data_path, encoding="utf-8-sig")
        dates = sorted(str(d) for d in df["date"].unique())
        target_col = f"target_{horizon}"
        hold_days = {"1d": 1, "3d": 3, "5d": 5}[horizon]

        if len(dates) < train_days + step:
            return {}

        benchmark_returns = self._load_benchmark_returns(benchmark_col, dates, target_col)

        period_returns = []
        benchmark_period = []
        monthly_pnls: Dict[str, float] = {}
        nav = 1.0
        peak_nav = 1.0
        max_dd = 0.0
        traded_dates = set()  # 避免重复交易

        for test_start in range(train_days, len(dates), step):
            test_end = min(test_start + step, len(dates))
            train_dates_list = dates[test_start - train_days:test_start]
            test_dates_list = dates[test_start:test_end]

            train = df[df["date"].astype(str).isin(train_dates_list)].copy()
            test = df[df["date"].astype(str).isin(test_dates_list)].copy()

            train_h = train.dropna(subset=ALL_FEATURES + [target_col])
            test_h = test.dropna(subset=ALL_FEATURES + [target_col])

            if len(train_h) < 100:
                continue

            X_train = train_h[ALL_FEATURES].values
            y_train = train_h[target_col].values.astype(float)

            scaler = StandardScaler()
            X_train_s = scaler.fit_transform(X_train)

            model = XGBRegressor(
                n_estimators=200, max_depth=5, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                objective="reg:pseudohubererror",
                random_state=42, verbosity=0,
            )
            model.fit(X_train_s, y_train)

            # 在测试窗口内逐日交易（非重叠入场）
            for test_date in test_dates_list:
                if test_date in traded_dates:
                    continue
                # 非重叠：跳过不够 hold_days 的日期
                date_idx = dates.index(test_date)
                if date_idx + hold_days > len(dates):
                    continue

                day_data = test_h[test_h["date"].astype(str) == test_date]
                if len(day_data) < top_n:
                    continue

                X_day = day_data[ALL_FEATURES].values
                X_day_s = scaler.transform(X_day)
                y_pred = model.predict(X_day_s)

                day_data = day_data.copy()
                day_data["pred"] = y_pred
                top = day_data.nlargest(top_n, "pred")
                avg_return = float(top[target_col].mean()) / 100

                net_return = avg_return - TRANSACTION_COST
                period_returns.append(net_return)
                nav *= (1 + net_return)

                if nav > peak_nav:
                    peak_nav = nav
                dd = (peak_nav - nav) / peak_nav
                if dd > max_dd:
                    max_dd = dd

                # 同期基准
                bm_cum = 0.0
                for j in range(hold_days):
                    if date_idx + j < len(dates):
                        bm_cum += benchmark_returns.get(dates[date_idx + j], 0.0)
                benchmark_period.append(bm_cum)

                month_key = test_date[:6]
                monthly_pnls[month_key] = monthly_pnls.get(month_key, 0.0) + net_return

                # 标记已交易日期（非重叠）
                for j in range(hold_days):
                    if date_idx + j < len(dates):
                        traded_dates.add(dates[date_idx + j])

        if not period_returns:
            return {}

        returns_arr = np.array(period_returns)
        benchmark_arr = np.array(benchmark_period)

        periods_per_year = 252 / hold_days

        excess = returns_arr - benchmark_arr
        annual_excess = float(np.mean(excess) * periods_per_year)
        annual_vol = float(np.std(excess) * np.sqrt(periods_per_year))
        ir = annual_excess / annual_vol if annual_vol > 0 else 0

        win_months = sum(1 for v in monthly_pnls.values() if v > 0)
        monthly_win_rate = win_months / len(monthly_pnls) if monthly_pnls else 0

        annual_return = float(np.mean(returns_arr) * periods_per_year)
        calmar = annual_return / max_dd if max_dd > 0 else 0

        metrics = {
            "horizon": horizon,
            "top_n": top_n,
            "hold_days": hold_days,
            "final_nav": round(nav, 4),
            "annual_return": round(annual_return, 4),
            "annual_excess_return": round(annual_excess, 4),
            "information_ratio": round(ir, 4),
            "monthly_win_rate": round(monthly_win_rate, 4),
            "max_drawdown": round(max_dd, 4),
            "calmar_ratio": round(calmar, 4),
            "n_periods": len(period_returns),
        }

        self.portfolio_metrics[horizon] = metrics

        if verbose:
            print(f"\n── 纯多头组合评估 [{horizon}] ──")
            print(f"  交易期数: {metrics['n_periods']} (每{hold_days}天)")
            print(f"  最终NAV: {metrics['final_nav']}")
            print(f"  年化收益: {metrics['annual_return']:.2%}")
            print(f"  年化超额收益: {metrics['annual_excess_return']:.2%}")
            print(f"  Information Ratio: {metrics['information_ratio']:.3f}")
            print(f"  月胜率: {metrics['monthly_win_rate']:.1%}")
            print(f"  最大回撤: {metrics['max_drawdown']:.2%}")
            print(f"  Calmar Ratio: {metrics['calmar_ratio']:.3f}")

        return metrics

    @staticmethod
    def _load_benchmark_returns(index_code: str, dates: List[str],
                                target_col: str) -> Dict[str, float]:
        """加载基准指数日收益（简化：使用 direction_data 中所有股票的平均收益作为代理）。"""
        try:
            import sys
            from pathlib import Path
            _data_root = Path(__file__).resolve().parents[3] / "data" / "backtest"
            _index_path = _data_root / "指数数据" / "index_daily" / f"{index_code}.parquet"
            if not _index_path.exists():
                return {}

            df = pd.read_parquet(_index_path)
            df = df.reset_index()
            df["date_str"] = df["trade_date"].dt.strftime("%Y%m%d")

            result = {}
            for d in dates:
                day_data = df[df["date_str"] == d]
                if not day_data.empty:
                    pct = float(day_data.iloc[0].get("pct_chg", 0) or 0) / 100
                    result[d] = pct
            return result
        except Exception:
            return {}

    # ── 预测 ─────────────────────────────────────────────

    def predict(self, features_df: pd.DataFrame, horizon: str = "5d") -> List[dict]:
        """为一批股票预测预期持有期收益率。

        Args:
            features_df: DataFrame with ALL_FEATURES columns, index = symbol
            horizon: "1d", "3d", or "5d"

        Returns:
            [{symbol, expected_return, ...}, ...] 按 expected_return 降序
        """
        if horizon not in self.models:
            return self._predict_fallback(features_df)

        model = self.models[horizon]
        scaler = self.scalers.get(horizon)

        X = features_df[ALL_FEATURES].fillna(0).values
        if scaler:
            X = scaler.transform(X)

        y_pred = model.predict(X)  # 预期持有期收益率 (%)

        results = []
        for i, sym in enumerate(features_df.index):
            results.append({
                "symbol": sym,
                "expected_return": round(float(y_pred[i]), 2),
            })

        results.sort(key=lambda x: x["expected_return"], reverse=True)
        return results

    def _predict_fallback(self, features_df: pd.DataFrame) -> List[dict]:
        """模型未训练时的启发式回退：T 日动量 + 成交量排序。"""
        results = []
        for i, sym in enumerate(features_df.index):
            row = features_df.iloc[i]
            pct = float(row.get("change_pct", 0) or 0)
            vol_ratio = float(row.get("vol_ratio_1d", 1.0) or 1.0)
            score = pct * 0.15 + (vol_ratio - 1.0) * 1.5 * (1 if pct > 0 else -1)
            results.append({
                "symbol": sym,
                "expected_return": round(score, 2),
            })
        results.sort(key=lambda x: x["expected_return"], reverse=True)
        return results

    # ── 模型持久化 ────────────────────────────────────────

    def save(self, path: Optional[Path] = None):
        """保存模型、scaler、指标（不含 calibrators/regime classifier）。"""
        save_path = Path(path) if path else _get_data_dir() / "direction_model.pkl"
        Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        with open(save_path, "wb") as f:
            pickle.dump({
                "models": self.models,
                "scalers": self.scalers,
                "metrics": self.metrics,
                "portfolio_metrics": self.portfolio_metrics,
            }, f)
        logger.info(f"模型已保存: {save_path}")

    def load(self, path: Optional[Path] = None) -> bool:
        """加载模型、scaler、指标。"""
        load_path = Path(path) if path else _get_data_dir() / "direction_model.pkl"
        if not load_path.exists():
            logger.warning(f"模型文件不存在: {load_path}")
            return False
        with open(load_path, "rb") as f:
            saved = pickle.load(f)
            self.models = saved.get("models", {})
            self.scalers = saved.get("scalers", {})
            self.metrics = saved.get("metrics", {})
            self.portfolio_metrics = saved.get("portfolio_metrics", {})
        logger.info(f"模型已加载: {load_path}")
        return True
