# -*- coding: utf-8 -*-
"""方向预测服务：大盘环境分类 + 个股方向分类器。

Two-stage architecture:
  Stage 1: MarketRegimeClassifier — 判断当日是否适合做多
  Stage 2: DirectionPredictionService — 输出每只股票 P(return > 0) for 1d/3d/5d
"""

import logging
import pickle
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 特征配置 ───────────────────────────────────────────────

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
]

CROSS_SECTIONAL_COLS = [
    "pct_rank", "amount_rank", "turnover_rank",
    "mf_rank", "vol_ratio_rank",
]

MARKET_COLS = [
    "up_ratio", "limit_up_count", "advance_decline",
    "mkt_mean", "mkt_vol", "mkt_amount_total",
]

INDEX_COLS = [
    "index_ret_5d", "index_rsi14", "index_ma20_deviation",
]

ALL_FEATURES = FEATURE_COLS + CROSS_SECTIONAL_COLS + MARKET_COLS + INDEX_COLS
HORIZONS = ["1d", "3d", "5d"]


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


# ── 特征推导 ───────────────────────────────────────────────

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
    """从日线和资金流向数据推导单只股票的方向特征。

    Args:
        daily_bars: 最近 30 根日线，每根含 close/open/vol/amount，按时间升序
        quote: 当日行情 dict，含 change_pct, turnover_rate
        moneyflow: 资金流向 dict，含 big_order_net/main_force_ratio/flow_5d_cum/available
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

        # --- 上涨/下跌日成交量比（买压信号）---
        if len(daily_bars) >= 6:
            up_vols, down_vols = [], []
            for i in range(-5, 0):
                if i + 1 < 0 and closes[i] > closes[i - 1]:
                    up_vols.append(volumes[i])
                elif i + 1 >= 0 or closes[i] < closes[i - 1]:
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
        })

    # --- 来自行情 ---
    f["change_pct"] = float(quote.get("change_pct", 0) or 0)
    f["turnover_rate"] = float(quote.get("turnover_rate", 0) or 0)

    # --- 资金流向 ---
    if moneyflow and moneyflow.get("available"):
        f["big_order_net"] = round(float(moneyflow.get("big_order_net", 0)) / 1e8, 2)
        f["main_force_ratio"] = round(float(moneyflow.get("main_force_ratio", 0)), 3)
        f["flow_5d_cum"] = round(float(moneyflow.get("flow_5d_cum", 0)) / 1e8, 2)
    else:
        f["big_order_net"] = 0.0
        f["main_force_ratio"] = 0.0
        f["flow_5d_cum"] = 0.0

    return f


def add_cross_sectional_ranks(features_data: Dict[str, dict]) -> Dict[str, dict]:
    """为所有股票的特征添加截面排名特征。

    在所有股票特征计算完毕后调用，将原始值转换为 0-1 的百分位排名，
    从而消除大盘噪音，突出个股相对强弱。

    Args:
        features_data: {symbol: {feature_name: value, ...}, ...}
    Returns:
        更新后的 features_data，每只股票新增 pct_rank/amount_rank 等字段
    """
    if not features_data:
        return features_data

    syms = list(features_data.keys())
    rank_fields = {
        "pct_rank": "change_pct",
        "amount_rank": "turnover_rate",  # turnover_rate ≈ amount proxy (已包含流动性)
        "turnover_rank": "turnover_rate",
        "mf_rank": "big_order_net",
        "vol_ratio_rank": "vol_ratio_1d",
    }

    for rank_col, src_col in rank_fields.items():
        vals = np.array([features_data[s].get(src_col, 0) for s in syms], dtype=float)
        # 百分位排名 (0-1)，nan 和 inf 安全处理
        valid = ~np.isnan(vals) & ~np.isinf(vals)
        ranks = np.full(len(syms), 0.5, dtype=float)
        if valid.sum() >= 2:
            from scipy.stats import rankdata
            ranks[valid] = (rankdata(vals[valid]) - 1) / (valid.sum() - 1)
        for i, sym in enumerate(syms):
            features_data[sym][rank_col] = round(float(ranks[i]), 4)

    # amount_rank 使用成交额代理（如果有），否则复用 turnover_rank
    # 因为 quote 中有 amount 字段但不在 features_data 里，这里通过 turnover_rate 近似
    # amount_rank 和 turnover_rank 高度相关，保留两者给模型自行选择

    return features_data


def compute_market_breadth(quotes: Dict[str, dict]) -> dict:
    """从当日行情截面计算市场宽度特征。"""
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
    """获取指数衍生特征（上证指数）。

    Returns:
        {index_ret_5d, index_rsi14, index_ma20_deviation}
        获取失败返回默认值。
    """
    defaults = {"index_ret_5d": 0.0, "index_rsi14": 50.0, "index_ma20_deviation": 0.0}

    try:
        start_dt = datetime.strptime(end_date, "%Y%m%d") - timedelta(days=50)
        start_date = start_dt.strftime("%Y%m%d")

        # 尝试 index_daily，失败则回退到 daily
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

        # index_ret_5d: 最近 5 日涨跌幅
        ret_5d = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if closes[-6] > 0 else 0

        # index_rsi14
        period = min(14, len(closes) - 1)
        if period > 0:
            diffs = [closes[i] - closes[i - 1] for i in range(-period, 0)]
            avg_gain = sum(max(d, 0) for d in diffs) / period
            avg_loss = sum(max(-d, 0) for d in diffs) / period
            rsi14 = round(100 - 100 / (1 + avg_gain / avg_loss), 1) if avg_loss > 0 else 100.0
        else:
            rsi14 = 50.0

        # index_ma20_deviation
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


# ── 大盘环境分类器 ─────────────────────────────────────────


class MarketRegimeClassifier:
    """大盘环境分类器：基于市场宽度和指数趋势判断当日是否适合做多。

    使用启发式规则（无需训练）：
      favorable: breadth > 20d_median AND mkt_mean > 20d_median
      neutral:   one of the above
      unfavorable: neither
    """

    def __init__(self, penalty: float = 0.7):
        self.penalty = penalty
        self._history: List[dict] = []
        self._lock = threading.Lock()

    def classify(self, breadth: dict,
                 index_features: Optional[dict] = None) -> Tuple[str, float]:
        """返回 (regime_label, confidence_multiplier)。

        Args:
            breadth: 市场宽度 dict（up_ratio, mkt_mean, limit_up_count...）
            index_features: 可选，指数衍生特征（index_ret_5d, index_rsi14...）
        """
        with self._lock:
            self._history.append(breadth)
            if len(self._history) > 20:
                self._history = self._history[-20:]

            up_ratio = breadth.get("up_ratio", 0.5)
            mkt_mean = breadth.get("mkt_mean", 0)

            # 计算 20 日滚动中位数（不含当日）
            if len(self._history) >= 5:
                hist_up = [h.get("up_ratio", 0.5) for h in self._history[:-1]]
                hist_mkt = [h.get("mkt_mean", 0) for h in self._history[:-1]]
                median_up = sorted(hist_up)[len(hist_up) // 2] if hist_up else 0.5
                median_mkt = sorted(hist_mkt)[len(hist_mkt) // 2] if hist_mkt else 0
            else:
                median_up = 0.5
                median_mkt = 0

            # 市场宽度判定：上涨占比高于近期中位数
            breadth_ok = up_ratio > median_up
            # 市场情绪判定：均值收益高于近期中位数（替代绝对阈值）
            sentiment_ok = mkt_mean > median_mkt

            # 指数辅助判定：若提供指数数据，指数 5 日收益 < -1% 则降一级
            if index_features:
                idx_ret = index_features.get("index_ret_5d", 0)
                if idx_ret < -1.0:
                    if breadth_ok and sentiment_ok:
                        return "neutral", self.penalty
                    elif breadth_ok or sentiment_ok:
                        return "unfavorable", self.penalty ** 2

            if breadth_ok and sentiment_ok:
                return "favorable", 1.0
            elif breadth_ok or sentiment_ok:
                return "neutral", self.penalty
            else:
                return "unfavorable", self.penalty ** 2


# ── 方向预测服务 ───────────────────────────────────────────


class DirectionPredictionService:
    """个股方向预测：为每只股票输出 P(return > 0)。

    使用 XGBoost 作二元分类，分三个独立模型（1d/3d/5d）。
    支持走步前进训练和概率校准。
    """

    def __init__(self):
        self.models: Dict[str, object] = {}      # horizon → trained XGBoost model
        self.scalers: Dict[str, object] = {}     # horizon → StandardScaler
        self.calibrators: Dict[str, object] = {} # horizon → IsotonicRegression
        self.metrics: Dict[str, dict] = {}       # horizon → validation metrics
        self._regime_classifier = MarketRegimeClassifier()

    # ── 训练 ─────────────────────────────────────────────

    @staticmethod
    def _tune_xgb(X, y, n_trials: int = 50, random_state: int = 42) -> dict:
        """用 Optuna 搜索 XGBoost 超参，返回最优参数字典。

        Args:
            X: 特征矩阵 (numpy array)
            y: 标签 (numpy array)
            n_trials: Optuna 试验次数
            random_state: 随机种子
        """
        import optuna
        from xgboost import XGBClassifier
        from sklearn.model_selection import TimeSeriesSplit
        from sklearn.metrics import roc_auc_score

        pos_ratio = y.mean()
        scale_weight = (1 - pos_ratio) / max(pos_ratio, 0.01)
        tscv = TimeSeriesSplit(n_splits=3)

        def objective(trial):
            params = {
                "max_depth": trial.suggest_int("max_depth", 3, 8),
                "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
                "n_estimators": trial.suggest_int("n_estimators", 100, 500),
                "subsample": trial.suggest_float("subsample", 0.6, 1.0),
                "colsample_bytree": trial.suggest_float("colsample_bytree", 0.6, 1.0),
                "reg_alpha": trial.suggest_float("reg_alpha", 1e-6, 1.0, log=True),
                "reg_lambda": trial.suggest_float("reg_lambda", 1e-6, 1.0, log=True),
                "min_child_weight": trial.suggest_int("min_child_weight", 1, 10),
                "scale_pos_weight": scale_weight,
                "random_state": random_state,
                "verbosity": 0,
                "early_stopping_rounds": 20,
            }

            aucs = []
            for train_idx, val_idx in tscv.split(X):
                X_tr, X_val = X[train_idx], X[val_idx]
                y_tr, y_val = y[train_idx], y[val_idx]

                model = XGBClassifier(**params)
                model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

                y_prob = model.predict_proba(X_val)[:, 1]
                auc = roc_auc_score(y_val, y_prob)
                aucs.append(auc)

                trial.report(auc, step=len(aucs) - 1)
                if trial.should_prune():
                    raise optuna.TrialPruned()

            return float(np.mean(aucs))

        sampler = optuna.samplers.TPESampler(seed=random_state)
        study = optuna.create_study(
            direction="maximize", sampler=sampler,
            pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
        )
        study.optimize(objective, n_trials=n_trials, show_progress_bar=n_trials > 20)

        return study.best_params

    def train_walk_forward(self, data_path: str, train_days: int = 120,
                           step: int = 10, n_trials: int = 50,
                           verbose: bool = True):
        """走步前进训练 + 验证（滚动窗口），产出三个 horizon 的模型和指标。

        Args:
            data_path: dump_direction_data.py 输出的 CSV 路径
            train_days: 每个窗口的训练天数（固定滚动窗口）
            step: 每轮向前滚动天数
            n_trials: Optuna 超参搜索试验次数（第一个窗口，0 则跳过）
        """
        from sklearn.preprocessing import StandardScaler
        from sklearn.isotonic import IsotonicRegression
        from xgboost import XGBClassifier

        df = pd.read_csv(data_path, encoding="utf-8-sig")
        dates = sorted(df["date"].unique())

        if len(dates) < train_days + step:
            raise ValueError(f"数据不足：{len(dates)} 天，需要至少 {train_days + step} 天")

        all_results = {h: [] for h in HORIZONS}
        tuned_params = {h: None for h in HORIZONS}

        is_first_window = True

        for test_start in range(train_days, len(dates), step):
            test_end = min(test_start + step, len(dates))
            train_dates = dates[test_start - train_days:test_start]  # 固定长度滚动窗口
            test_dates = dates[test_start:test_end]

            train = df[df["date"].isin(train_dates)].copy()
            test = df[df["date"].isin(test_dates)].copy()

            for horizon, target_col in zip(HORIZONS, ["target_1d", "target_3d", "target_5d"]):
                train_h = train.dropna(subset=ALL_FEATURES + [target_col])
                test_h = test.dropna(subset=ALL_FEATURES + [target_col])

                if len(train_h) < 100 or len(test_h) < 50:
                    continue

                X_train = train_h[ALL_FEATURES].values
                y_train = train_h[target_col].values.astype(int)
                X_test = test_h[ALL_FEATURES].values
                y_test = test_h[target_col].values.astype(int)

                scaler = StandardScaler()
                X_train_s = scaler.fit_transform(X_train)
                X_test_s = scaler.transform(X_test)

                pos_ratio = y_train.mean()
                scale_weight = (1 - pos_ratio) / max(pos_ratio, 0.01)

                # 第一个窗口：Optuna 超参搜索
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
                model = XGBClassifier(
                    n_estimators=best.get("n_estimators", 200),
                    max_depth=best.get("max_depth", 5),
                    learning_rate=best.get("learning_rate", 0.05),
                    subsample=best.get("subsample", 0.8),
                    colsample_bytree=best.get("colsample_bytree", 0.8),
                    reg_alpha=best.get("reg_alpha", 0.1),
                    reg_lambda=best.get("reg_lambda", 1.0),
                    min_child_weight=best.get("min_child_weight", 1),
                    scale_pos_weight=scale_weight,
                    random_state=42, verbosity=0,
                )
                model.fit(X_train_s, y_train)

                # 概率校准
                y_prob = model.predict_proba(X_test_s)[:, 1]
                try:
                    calib = IsotonicRegression(out_of_bounds="clip")
                    calib.fit(y_prob, y_test)
                    y_calib = calib.predict(y_prob)
                except Exception:
                    calib = None
                    y_calib = y_prob

                baseline = max(y_test.mean(), 1 - y_test.mean())
                correct = (y_calib >= 0.5).astype(int) == y_test
                model_acc = float(correct.astype(float).mean())

                all_results[horizon].append({
                    "window": f"{test_dates[0]}~{test_dates[-1]}",
                    "n_train": len(train_h), "n_test": len(test_h),
                    "accuracy": model_acc, "baseline": baseline,
                    "pos_ratio": float(y_test.mean()),
                })

                # 保存最后一个窗口的模型
                self.models[horizon] = model
                self.scalers[horizon] = scaler
                self.calibrators[horizon] = calib

                if verbose:
                    delta = model_acc - baseline
                    print(f"  {test_dates[0]}~{test_dates[-1]} {horizon}: "
                          f"acc={model_acc:.1%} base={baseline:.1%} Δ={delta:+.1%}")

            is_first_window = False

        # 汇总指标
        for horizon in HORIZONS:
            if all_results[horizon]:
                accs = [r["accuracy"] for r in all_results[horizon]]
                bases = [r["baseline"] for r in all_results[horizon]]
                self.metrics[horizon] = {
                    "accuracy_mean": float(np.mean(accs)),
                    "baseline_mean": float(np.mean(bases)),
                    "delta_mean": float(np.mean(accs) - np.mean(bases)),
                    "n_windows": len(accs),
                }

        return all_results

    # ── 预测 ─────────────────────────────────────────────

    def predict(self, features_df: pd.DataFrame, horizon: str = "5d",
                breadth: Optional[dict] = None,
                index_features: Optional[dict] = None) -> List[dict]:
        """为一批股票预测上涨概率。

        Args:
            features_df: DataFrame with ALL_FEATURES columns, index = symbol
            horizon: "1d", "3d", or "5d"
            breadth: 市场宽度 dict（若提供则进行环境判断）
            index_features: 指数衍生特征（index_ret_5d 等）

        Returns:
            [{symbol, up_probability, confidence, regime_label}, ...]
        """
        if horizon not in self.models:
            return self._predict_fallback(features_df, breadth, index_features)

        model = self.models[horizon]
        scaler = self.scalers.get(horizon)
        calibrator = self.calibrators.get(horizon)

        # 大盘环境
        regime_label = "favorable"
        confidence_mult = 1.0
        if breadth:
            regime_label, confidence_mult = self._regime_classifier.classify(
                breadth, index_features)

        # 特征预测
        X = features_df[ALL_FEATURES].fillna(0).values
        if scaler:
            X = scaler.transform(X)

        probs = model.predict_proba(X)[:, 1]

        if calibrator:
            try:
                probs = calibrator.predict(probs)
            except Exception:
                pass

        results = []
        for i, sym in enumerate(features_df.index):
            raw_prob = float(probs[i])
            confidence = round(raw_prob * confidence_mult, 3)
            results.append({
                "symbol": sym,
                "up_probability": round(raw_prob, 3),
                "confidence": confidence,
                "regime_label": regime_label,
                "confidence_multiplier": round(confidence_mult, 3),
            })

        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results

    def _predict_fallback(self, features_df: pd.DataFrame,
                          breadth: Optional[dict] = None,
                          index_features: Optional[dict] = None) -> List[dict]:
        """模型未训练时的启发式回退预测。

        以市场宽度 up_ratio 为基础概率，用个股涨跌幅和量比做区分。
        """
        regime_label, confidence_mult = "favorable", 1.0
        if breadth:
            regime_label, confidence_mult = self._regime_classifier.classify(
                breadth, index_features)

        up_ratio = (breadth or {}).get("up_ratio", 0.5)
        results = []
        for i, sym in enumerate(features_df.index):
            row = features_df.iloc[i]
            pct = float(row.get("change_pct", 0) or 0)
            vol_ratio = float(row.get("vol_ratio_1d", 1.0) or 1.0)

            # change_pct 映射为方向偏差（+5% → +0.08, -5% → -0.08）
            pct_bias = pct / 60
            # 放量上涨加分，放量下跌减分
            vol_bias = (vol_ratio - 1.0) * 0.05 * (1 if pct > 0 else -1)

            raw_prob = up_ratio + pct_bias + vol_bias
            raw_prob = max(0.05, min(0.95, raw_prob))

            results.append({
                "symbol": sym,
                "up_probability": round(raw_prob, 3),
                "confidence": round(raw_prob * confidence_mult, 3),
                "regime_label": regime_label,
                "confidence_multiplier": round(confidence_mult, 3),
            })
        results.sort(key=lambda x: x["confidence"], reverse=True)
        return results
