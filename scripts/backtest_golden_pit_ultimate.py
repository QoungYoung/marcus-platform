# -*- coding: utf-8 -*-
"""黄金坑终极回测 v2 — DCA 定投全维度优化 (修正 CAGR 计算)

核心修正: CAGR 使用日历跨度 (首发→末出), 而非仅持仓天数, 避免小样本膨化
新增: 资本效率、最少交易笔数过滤、频率加权评分

Phase 1: 入场参数穷举 → 固定持有30天 → 按频率加权评分筛选
Phase 2: 出场策略穷举 → 用 Phase 1 最优入场 → 按年化收益排序
Phase 3: Walk-forward 验证 + 逐年拆解 + 参数敏感度

优化目标: 年化收益率(CAGR) × √(交易频率) × 资本效率
每个指数独立寻优, 全9指数覆盖
"""

import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from itertools import product
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import dotenv
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    dotenv.load_dotenv(_env_path)


# ═══════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════

FULL_SERIES_URL = "https://arkvol.com/api/funds-greed/alla/series?range=full"

INDICES: Dict[str, Dict[str, Any]] = {
    "588000": {"name": "科创50",   "market": "A股", "min_history": 120},
    "510500": {"name": "中证500",  "market": "A股", "min_history": 120},
    "159845": {"name": "中证1000", "market": "A股", "min_history": 120},
    "159915": {"name": "创业板指", "market": "A股", "min_history": 120},
    "510300": {"name": "沪深300",  "market": "A股", "min_history": 120},
    "510050": {"name": "上证50",   "market": "A股", "min_history": 120},
    "513400": {"name": "道琼斯指数","market": "美股", "min_history": 60},
    "159632": {"name": "纳斯达克", "market": "美股", "min_history": 60},
    "513600": {"name": "恒生指数", "market": "港股", "min_history": 120},
}

ROLLING_WINDOW = 500
MIN_WINDOW = 50
PIT_WINDOW_DAYS = 15
SIGNAL_COOLDOWN = 5
MIN_TRADES = 8  # 最少交易笔数，确保统计有意义
MAX_HOLD = 60   # 最大持仓等待天数

# ── Phase 1: 入场参数 ──
TURNING_DAYS = [0, 1, 2, 3]
PCT_THRESHOLDS = [5, 8, 10, 12, 15, 18, 20, 25]  # 分位阈值
ZSCORE_THRESHOLDS = [-2.5, -2.0, -1.5, -1.0, -0.5]
DCA_STRATEGIES = [
    "uniform_3", "uniform_5", "uniform_7", "uniform_10", "uniform_15",
    "front_loaded", "back_loaded", "triangle", "lump_entry",
]
POSITION_WEIGHTINGS = ["none", "linear", "squared"]

# ── Phase 2: 退出策略 ──
FIXED_HOLD_DAYS = [10, 15, 20, 25, 30, 40, 50, 60]

STAGED_HALF_PCTS = [25, 30, 35, 40, 45]
STAGED_FULL_PCTS = [40, 50, 60, 70, 80]

FULL_EXIT_PCTS = [30, 40, 50, 60, 70, 80]

TRAILING_STOP_PCTS = [5, 10, 15, 20]

FALLBACK_DAYS = [20, 30, 40, 50, 60]

COMBINED_HALF_PCTS = [30, 40]
COMBINED_FULL_PCTS = [50, 60, 70]
COMBINED_STOP_DAYS = [2, 3]

TIME_DECAY_SCHEDULES = [
    {"name": "aggressive", "steps": [(10, 50), (20, 40), (30, 30), (40, 20)]},
    {"name": "moderate",   "steps": [(15, 50), (30, 40), (45, 30), (60, 20)]},
    {"name": "lenient",    "steps": [(20, 50), (40, 35), (60, 25)]},
]


# ═══════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════

def read_api_key() -> str:
    env_key = os.environ.get("ARKVOL_API_KEY", "").strip()
    if env_key:
        return env_key
    for env_path in [PROJECT_ROOT / ".env", Path(".env")]:
        if env_path.is_file():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ARKVOL_API_KEY="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
    raise RuntimeError("未配置 ARKVOL_API_KEY")


def fetch_full_series(api_key: str) -> Dict[str, List[Dict]]:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
    req = Request(FULL_SERIES_URL, headers={
        "X-API-Key": api_key, "Accept": "application/json",
    }, method="GET")
    for attempt in range(3):
        try:
            with urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
            if isinstance(data, dict):
                d = data.get("data", data)
                if isinstance(d, dict):
                    return d
                raise RuntimeError(f"Unexpected: {str(data)[:200]}")
            raise RuntimeError(f"Unexpected: {str(data)[:200]}")
        except (HTTPError, URLError) as e:
            if attempt < 2:
                time.sleep(2)
                continue
            raise RuntimeError(f"请求失败: {e}")


# ═══════════════════════════════════════════════════════════════════════
# ROLLING PERCENTILE & ZSCORE
# ═══════════════════════════════════════════════════════════════════════

def compute_rolling_percentile(values: np.ndarray, window: int = ROLLING_WINDOW,
                               min_window: int = MIN_WINDOW) -> np.ndarray:
    n = len(values)
    pct = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        start = max(0, i - window + 1)
        wv = values[start:i + 1]
        if len(wv) >= min_window:
            pct[i] = (wv < values[i]).sum() / len(wv) * 100.0
    return pct


def compute_zscore(values: np.ndarray, window: int = ROLLING_WINDOW,
                   min_window: int = MIN_WINDOW) -> np.ndarray:
    n = len(values)
    zs = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        start = max(0, i - window + 1)
        wv = values[start:i + 1]
        if len(wv) >= min_window:
            mu, sd = np.mean(wv), np.std(wv)
            if sd > 0:
                zs[i] = (values[i] - mu) / sd
    return zs


# ═══════════════════════════════════════════════════════════════════════
# DCA WEIGHTS
# ═══════════════════════════════════════════════════════════════════════

def make_dca_weights(strategy: str) -> np.ndarray:
    n = PIT_WINDOW_DAYS
    strats = {
        "uniform_3":    np.array([1.0]*3 + [0.0]*12),
        "uniform_5":    np.array([1.0]*5 + [0.0]*10),
        "uniform_7":    np.array([1.0]*7 + [0.0]*8),
        "uniform_10":   np.array([1.0]*10 + [0.0]*5),
        "uniform_15":   np.ones(n),
        "front_loaded": np.array([n - i for i in range(n)], dtype=float),
        "back_loaded":  np.array([i + 1 for i in range(n)], dtype=float),
        "triangle":     np.array([(i+1) if i < 7 else (n-i) for i in range(n)], dtype=float),
        "lump_entry":   np.array([1.0] + [0.0]*14),
    }
    raw = strats.get(strategy, strats["uniform_10"])
    return raw / raw.sum()


# ═══════════════════════════════════════════════════════════════════════
# ENTRY / EXIT SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════════════

def get_signal_strength(greed_val: np.ndarray, pct_val: float, zscore_val: float,
                        i: int, threshold_type: str, threshold_value: float) -> float:
    """0-1 signal strength for entry. Higher = signal is further below threshold."""
    if threshold_type == "fixed":
        if threshold_value <= 0:
            return 0
        return min(1.0, max(0.0, 1.0 - float(greed_val[i]) / threshold_value))
    elif threshold_type == "pct":
        if threshold_value <= 0:
            return 0
        return min(1.0, max(0.0, 1.0 - pct_val / threshold_value))
    elif threshold_type == "zscore":
        if threshold_value >= 0:
            return 0
        return min(1.0, abs(zscore_val) / abs(threshold_value))
    return 0


def is_entry_signal(greeds: np.ndarray, pct: np.ndarray, zscore: np.ndarray,
                    i: int, threshold_type: str, threshold_value: float) -> bool:
    """Check if day i triggers an entry signal."""
    if threshold_type == "fixed":
        if greeds[i] > threshold_value:
            return False
    elif threshold_type == "pct":
        if np.isnan(pct[i]) or pct[i] > threshold_value:
            return False
    elif threshold_type == "zscore":
        if np.isnan(zscore[i]) or zscore[i] > threshold_value:
            return False
    else:
        return False
    return True


def check_turning(greeds: np.ndarray, i: int, days: int, n: int) -> Tuple[bool, int]:
    """Check if greed has risen for `days` consecutive days. Returns (confirmed, entry_idx)."""
    if days <= 0:
        return True, i
    if i + days >= n:
        return False, i
    for j in range(1, days + 1):
        if greeds[i + j] <= greeds[i + j - 1]:
            return False, i
    return True, i + days


def get_exit_threshold_for_day(exit_type: str, exit_config: Dict, holding_days: int,
                                cur_pct: float) -> Tuple[Optional[str], Optional[int]]:
    """For staged/full_only/time_decay: get the exit threshold for current holding day.
    Returns (signal_type_or_None, threshold_pct_or_None)."""
    if exit_type == "staged":
        if cur_pct >= exit_config["full_exit_pct"]:
            return "full_exit", exit_config["full_exit_pct"]
        if cur_pct >= exit_config["half_exit_pct"]:
            return "half_exit", exit_config["half_exit_pct"]
        if holding_days >= exit_config.get("fallback_days", 60):
            return "fallback", None
    elif exit_type == "full_only":
        if cur_pct >= exit_config["exit_pct"]:
            return "full_exit", exit_config["exit_pct"]
        if holding_days >= exit_config.get("fallback_days", 60):
            return "fallback", None
    elif exit_type == "time_decay":
        schedule = sorted(exit_config.get("decay_steps", [(60, 25)]))
        threshold = None
        for days, thresh in schedule:
            if holding_days <= days:
                threshold = thresh
                break
        if threshold is None:
            threshold = schedule[-1][1]
        if cur_pct >= threshold:
            return "full_exit", threshold
    elif exit_type == "combined":
        if cur_pct >= exit_config["full_exit_pct"]:
            return "full_exit", exit_config["full_exit_pct"]
        if cur_pct >= exit_config["half_exit_pct"]:
            return "half_exit", exit_config["half_exit_pct"]
        if holding_days >= exit_config.get("fallback_days", 40):
            return "fallback", None
    return None, None


def check_stop_profit(greeds: np.ndarray, pct: np.ndarray,
                       entry_idx: int, current_idx: int, stop_days: int) -> bool:
    """Check if consecutive greed decline triggers stop-profit."""
    decline = 0
    for j in range(current_idx, max(entry_idx, current_idx - stop_days), -1):
        if j > entry_idx and greeds[j] < greeds[j - 1]:
            decline += 1
        else:
            break
    if decline < stop_days:
        return False
    # Peak check
    window_pct = pct[max(0, current_idx - 10):current_idx + 1]
    peak = np.nanmax(window_pct) if len(window_pct) > 0 else 0
    return peak >= 30


def check_trailing_stop(pct: np.ndarray, entry_idx: int, current_idx: int,
                         trail_pct: float) -> bool:
    """Check if greed percentile has dropped trail_pct from peak since entry."""
    window_pct = pct[entry_idx:current_idx + 1]
    peak = np.nanmax(window_pct)
    cur = pct[current_idx]
    if np.isnan(peak) or np.isnan(cur):
        return False
    return (peak - cur) >= trail_pct


# ═══════════════════════════════════════════════════════════════════════
# CORE: FULL DCA SIMULATION WITH DAY-BY-DAY EXIT TRACKING
# ═══════════════════════════════════════════════════════════════════════

def simulate_dca(
    greeds: np.ndarray,
    closes: np.ndarray,
    pct: np.ndarray,
    zscore: np.ndarray,
    dates: List[str],
    entry_config: Dict[str, Any],
    exit_config: Dict[str, Any],
    min_history: int,
    exec_delay: int = 1,
) -> Dict[str, Any]:
    """Full day-by-day DCA simulation. Returns metrics dict.

    Corrected CAGR: uses full calendar span (first_entry → last_exit), not just holding days.

    exec_delay=0: idealized same-day execution (buy from signal confirmation day).
    exec_delay=1: realistic T+1 execution (data arrives after close, buy next trading day).
    """
    n = len(greeds)
    n_trading_days = len(greeds) - min_history

    dca_weights = make_dca_weights(entry_config["dca_strategy"])
    thresh_type = entry_config["threshold_type"]
    thresh_val = entry_config["threshold_value"]
    turning_days = entry_config["turning_days"]
    pos_weighting = entry_config["position_weighting"]
    exit_type = exit_config.get("type", "fixed_hold")

    trades = []
    last_signal_idx = -999
    skip_until = -1

    # Phase 1: find all signals first
    signals = []
    for i in range(min_history, n - MAX_HOLD - PIT_WINDOW_DAYS):
        if i < skip_until:
            continue
        if i - last_signal_idx < SIGNAL_COOLDOWN:
            continue
        if not is_entry_signal(greeds, pct, zscore, i, thresh_type, thresh_val):
            continue
        confirmed, entry_idx = check_turning(greeds, i, turning_days, n)
        if not confirmed:
            continue
        if entry_idx + exec_delay + PIT_WINDOW_DAYS + MAX_HOLD >= n:
            continue
        if np.isnan(closes[entry_idx]) or closes[entry_idx] <= 0:
            continue

        last_signal_idx = i
        signals.append((i, entry_idx))
        skip_until = entry_idx + SIGNAL_COOLDOWN

    empty = {"trades": 0, "cagr": 0.0, "adj_cagr": 0.0, "win_rate": 0.0, "score": -999,
             "signals_found": 0, "details": [], "trades_per_year": 0.0,
             "capital_efficiency": 0.0, "avg_return": 0.0, "median_return": 0.0,
             "sharpe": 0.0, "max_drawdown": 0.0,
             "mae_mean": 0.0, "mae_median": 0.0, "mae_min": 0.0, "mae_max": 0.0,
             "mae_distribution": {},
             "profit_factor": 0.0,
             "calendar_span": 0, "max_concurrent": 0}
    if len(signals) == 0:
        return empty

    # Phase 2: execute each signal (DCA entry + dynamic exit)
    for sig_idx, entry_idx in signals:
        # ── DCA entry (delayed by exec_delay trading days) ──
        buy_start = entry_idx + exec_delay
        buy_prices = []
        buy_weights = []
        for d in range(PIT_WINDOW_DAYS):
            day = buy_start + d
            if day < n and dca_weights[d] > 0 and closes[day] > 0 and not np.isnan(closes[day]):
                buy_prices.append(closes[day])
                buy_weights.append(dca_weights[d])

        if len(buy_prices) == 0:
            continue

        buy_prices = np.array(buy_prices)
        buy_weights = np.array(buy_weights)
        buy_weights = buy_weights / buy_weights.sum()
        avg_entry = float(np.average(buy_prices, weights=buy_weights))

        # Position weighting multiplier
        strength = get_signal_strength(
            greeds, pct[sig_idx] if not np.isnan(pct[sig_idx]) else 0,
            zscore[sig_idx] if not np.isnan(zscore[sig_idx]) else 0,
            sig_idx, thresh_type, thresh_val,
        )
        if pos_weighting == "linear":
            multiplier = 1.0 + strength
        elif pos_weighting == "squared":
            multiplier = 1.0 + strength ** 2
        else:
            multiplier = 1.0

        # ── Day-by-day exit (timing references buy_start, not entry_idx) ──
        half_exited = False
        full_exited = False
        exit_date_idx = buy_start + PIT_WINDOW_DAYS  # default: last DCA day
        exit_type_used = "fallback"
        total_ret = 0.0
        min_close = avg_entry  # track lowest close since entry for MAE

        # For trailing stop: track peak greed percentile since buy_start
        peak_pct = np.nanmax(pct[buy_start:buy_start + 1])

        start_check = buy_start + 1  # start checking after first buy
        max_check = min(buy_start + MAX_HOLD + 1, n)

        for j in range(start_check, max_check):
            if full_exited:
                break
            if np.isnan(pct[j]):
                continue

            cur_pct = pct[j]
            holding_days = j - buy_start  # calendar days since first buy

            # Track lowest close since entry for MAE
            if closes[j] > 0 and not np.isnan(closes[j]) and closes[j] < min_close:
                min_close = closes[j]

            # Update trailing peak
            if not np.isnan(cur_pct) and cur_pct > peak_pct:
                peak_pct = cur_pct

            # Fixed hold exit
            if exit_type == "fixed_hold":
                target_day = buy_start + exit_config.get("hold_days", 30)
                if j >= target_day:
                    exit_date_idx = min(target_day, n - 1)
                    total_ret = (closes[exit_date_idx] - avg_entry) / avg_entry
                    exit_type_used = "fixed_hold"
                    full_exited = True
                    break
                continue

            # Staged / full_only / combined exit
            if exit_type in ("staged", "full_only", "combined"):
                sig, _ = get_exit_threshold_for_day(exit_type, exit_config, holding_days, cur_pct)

                if sig == "half_exit" and not half_exited:
                    half_ret = (closes[j] - avg_entry) / avg_entry
                    half_exited = True
                    exit_type_used = "half_exit"

                elif sig in ("full_exit", "fallback") and not full_exited:
                    if half_exited:
                        full_ret = (closes[j] - avg_entry) / avg_entry
                        total_ret = 0.5 * half_ret + 0.5 * full_ret
                    else:
                        total_ret = (closes[j] - avg_entry) / avg_entry
                    exit_date_idx = j
                    exit_type_used = sig
                    full_exited = True
                    break

                elif sig == "fallback" and not full_exited:
                    if half_exited:
                        total_ret = half_ret * 0.5 + (closes[j] - avg_entry) / avg_entry * 0.5
                    else:
                        total_ret = (closes[j] - avg_entry) / avg_entry
                    exit_date_idx = j
                    exit_type_used = "fallback"
                    full_exited = True
                    break

            # Combined: stop-profit check
            if exit_type == "combined" and not full_exited:
                if check_stop_profit(greeds, pct, buy_start, j,
                                      exit_config.get("stop_profit_days", 2)):
                    if half_exited:
                        full_ret = (closes[j] - avg_entry) / avg_entry
                        total_ret = 0.5 * half_ret + 0.5 * full_ret
                    else:
                        total_ret = (closes[j] - avg_entry) / avg_entry
                    exit_date_idx = j
                    exit_type_used = "stop_profit"
                    full_exited = True
                    break

            # Trailing stop exit
            if exit_type == "trailing_stop" and not full_exited:
                trail = exit_config.get("trail_pct", 10)
                if check_trailing_stop(pct, buy_start, j, trail):
                    total_ret = (closes[j] - avg_entry) / avg_entry
                    exit_date_idx = j
                    exit_type_used = "stop_profit"
                    full_exited = True
                    break
                if holding_days >= exit_config.get("fallback_days", 50):
                    total_ret = (closes[j] - avg_entry) / avg_entry
                    exit_date_idx = j
                    exit_type_used = "fallback"
                    full_exited = True
                    break

            # Time decay exit
            if exit_type == "time_decay" and not full_exited:
                sig, _ = get_exit_threshold_for_day(exit_type, exit_config, holding_days, cur_pct)
                if sig == "full_exit":
                    total_ret = (closes[j] - avg_entry) / avg_entry
                    exit_date_idx = j
                    exit_type_used = "time_decay"
                    full_exited = True
                    break

        # If never exited (safety)
        if not full_exited:
            exit_date_idx = min(buy_start + MAX_HOLD, n - 1)
            total_ret = (closes[exit_date_idx] - avg_entry) / avg_entry
            exit_type_used = "fallback"
            # Track min close through safety exit
            if closes[exit_date_idx] > 0 and not np.isnan(closes[exit_date_idx]) and closes[exit_date_idx] < min_close:
                min_close = closes[exit_date_idx]

        hold_cal_days = exit_date_idx - buy_start
        max_adverse_excursion = (min_close / avg_entry - 1) if avg_entry > 0 else 0

        trades.append({
            "entry_date_idx": buy_start,
            "exit_date_idx": exit_date_idx,
            "avg_entry_price": round(avg_entry, 4),
            "exit_price": round(float(closes[exit_date_idx]), 4),
            "return": round(float(total_ret), 4),
            "max_adverse_excursion": round(float(max_adverse_excursion), 4),
            "holding_days": hold_cal_days,
            "exit_signal": exit_type_used,
            "signal_strength": round(strength, 4),
            "n_buys": len(buy_prices),
            "multiplier": round(multiplier, 2),
        })

    # ── Max concurrent positions ──
    events = []
    for t in trades:
        events.append((t["entry_date_idx"], +1))
        events.append((t["exit_date_idx"], -1))
    events.sort()
    cur, max_conc = 0, 0
    for _, delta in events:
        cur += delta
        max_conc = max(max_conc, cur)

    # ── Metrics ──
    if len(trades) < 3:
        r = dict(empty)
        r["trades"] = len(trades)
        r["signals_found"] = len(signals)
        r["details"] = trades
        return r

    returns = np.array([t["return"] for t in trades])
    win_rate = float(np.sum(returns > 0) / len(returns))

    # Correct CAGR: use full calendar span
    first_entry = trades[0]["entry_date_idx"]
    last_exit = trades[-1]["exit_date_idx"]
    calendar_span = max(1, last_exit - first_entry)

    total_factor = float(np.prod(1.0 + returns))
    if total_factor > 0 and calendar_span > 0:
        cagr = float(total_factor ** (252.0 / calendar_span) - 1.0)
    else:
        cagr = 0.0

    # Capital efficiency: what fraction of calendar days is capital deployed?
    total_hold_days = sum(t["holding_days"] for t in trades)
    cap_efficiency = total_hold_days / max(1, calendar_span)

    avg_ret = float(np.mean(returns))
    med_ret = float(np.median(returns))
    trades_per_year = len(trades) / (calendar_span / 252.0) if calendar_span > 0 else 0

    # Sharpe
    if len(returns) >= 3:
        std_ret = float(np.std(returns))
        sharpe = float(avg_ret / std_ret * np.sqrt(trades_per_year)) if std_ret > 0 else 0
    else:
        sharpe = 0.0

    # Max drawdown per trade
    max_dd = float(np.min(returns))

    # ── Intra-trade MAE (Maximum Adverse Excursion) ──
    maes = np.array([t.get("max_adverse_excursion", 0) for t in trades])
    mae_mean = float(np.mean(maes))
    mae_median = float(np.median(maes))
    mae_min = float(np.min(maes))
    mae_max = float(np.max(maes))

    # MAE distribution buckets
    mae_distribution = {
        "p5": int(np.sum(maes < -0.05)),
        "p10": int(np.sum(maes < -0.10)),
        "p15": int(np.sum(maes < -0.15)),
        "p20": int(np.sum(maes < -0.20)),
        "p30": int(np.sum(maes < -0.30)),
    }

    # Profit factor
    profits = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = float(profits / losses) if losses > 0 else (10.0 if profits > 0 else 0)

    # Capital-pool CAGR: simulate investing with max_conc units of capital.
    # Each trade uses 1 unit; returns sum to total; capital is reused when positions close.
    # Effective return = sum(returns) / max_conc (sum of per-unit returns)
    # Then compound: (1 + total_return/max_conc)^(252/calendar_span) - 1
    total_abs_return = float(np.sum(returns))
    pool_capital = max(max_conc, 1)
    pool_return = total_abs_return / pool_capital
    pool_factor = 1.0 + pool_return
    if pool_factor > 0 and calendar_span > 0:
        pool_cagr = float(pool_factor ** (252.0 / calendar_span) - 1.0)
    else:
        pool_cagr = 0.0
    adj_cagr = pool_cagr

    # ── Score: adjusted_CAGR × frequency_weight × win_rate ──
    if len(trades) < MIN_TRADES:
        freq_weight = (len(trades) / MIN_TRADES) ** 0.5
    else:
        freq_weight = min(2.0, np.sqrt(len(trades) / MIN_TRADES))
    score = float(adj_cagr * freq_weight * win_rate)

    return {
        "trades": len(trades),
        "signals_found": len(signals),
        "calendar_span": int(calendar_span),
        "cagr": round(cagr, 4),
        "adj_cagr": round(adj_cagr, 4),
        "win_rate": round(win_rate, 4),
        "avg_return": round(avg_ret, 4),
        "median_return": round(med_ret, 4),
        "sharpe": round(sharpe, 4),
        "max_drawdown": round(max_dd, 4),
        "mae_mean": round(mae_mean, 4),
        "mae_median": round(mae_median, 4),
        "mae_min": round(mae_min, 4),
        "mae_max": round(mae_max, 4),
        "mae_distribution": mae_distribution,
        "profit_factor": round(profit_factor, 4),
        "trades_per_year": round(trades_per_year, 2),
        "capital_efficiency": round(cap_efficiency, 4),
        "max_concurrent": int(max_conc),
        "score": round(score, 4),
        "details": trades,
    }


# ═══════════════════════════════════════════════════════════════════════
# PHASE 1: ENTRY OPTIMIZATION (固定持有30天)
# ═══════════════════════════════════════════════════════════════════════

def build_fixed_thresholds(values: np.ndarray) -> List[float]:
    """Use P5, P8, P10, P12, P15, P18, P20, P25 from full distribution."""
    s = np.sort(values)
    n = len(s)
    result = []
    for p in [5, 8, 10, 12, 15, 18, 20, 25]:
        idx = min(int(n * p / 100), n - 1)
        val = round(float(s[idx]), 3)
        if val not in result:
            result.append(val)
    return sorted(set(result))


def run_phase1(
    greeds: np.ndarray, closes: np.ndarray, pct: np.ndarray,
    zscore: np.ndarray, dates: List[str], min_history: int, name: str,
    exec_delay: int = 1,
) -> List[Dict]:
    """Test all entry combos with fixed 30-day hold."""
    fixed_thresholds = build_fixed_thresholds(greeds)
    default_exit = {"type": "fixed_hold", "hold_days": 30}

    results = []
    threshold_sets = [
        ("fixed", fixed_thresholds),
        ("pct", PCT_THRESHOLDS),
        ("zscore", ZSCORE_THRESHOLDS),
    ]
    total = sum(len(tl) * len(TURNING_DAYS) * len(DCA_STRATEGIES) * len(POSITION_WEIGHTINGS)
                for _, tl in threshold_sets)

    n = 0
    for thresh_type, thresh_list in threshold_sets:
        for thresh_val in thresh_list:
            for turning in TURNING_DAYS:
                for strategy in DCA_STRATEGIES:
                    for weighting in POSITION_WEIGHTINGS:
                        n += 1
                        entry = {
                            "threshold_type": thresh_type,
                            "threshold_value": thresh_val,
                            "turning_days": turning,
                            "dca_strategy": strategy,
                            "position_weighting": weighting,
                        }
                        r = simulate_dca(
                            greeds, closes, pct, zscore, dates,
                            entry, default_exit, min_history, exec_delay,
                        )
                        results.append({
                            **entry,
                            **{k: v for k, v in r.items() if k != "details"},
                            "_d": len(r.get("details", [])),
                        })

    results.sort(key=lambda x: (x["trades"] >= MIN_TRADES, x["score"]), reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════════
# PHASE 2: EXIT OPTIMIZATION
# ═══════════════════════════════════════════════════════════════════════

def build_exit_configs() -> List[Dict]:
    configs = []
    # Fixed hold
    for d in FIXED_HOLD_DAYS:
        configs.append({"type": "fixed_hold", "hold_days": d})
    # Staged
    for hf, ff in product(STAGED_HALF_PCTS, STAGED_FULL_PCTS):
        if ff <= hf:
            continue
        for fb in FALLBACK_DAYS:
            configs.append({"type": "staged", "half_exit_pct": hf,
                            "full_exit_pct": ff, "fallback_days": fb})
    # Full only
    for ep in FULL_EXIT_PCTS:
        for fb in FALLBACK_DAYS:
            configs.append({"type": "full_only", "exit_pct": ep, "fallback_days": fb})
    # Trailing stop
    for tr in TRAILING_STOP_PCTS:
        for fb in FALLBACK_DAYS:
            configs.append({"type": "trailing_stop", "trail_pct": tr, "fallback_days": fb})
    # Time decay
    for sched in TIME_DECAY_SCHEDULES:
        configs.append({"type": "time_decay", "decay_steps": sched["steps"]})
    # Combined
    for hf, ff in product(COMBINED_HALF_PCTS, COMBINED_FULL_PCTS):
        if ff <= hf:
            continue
        for sd in COMBINED_STOP_DAYS:
            for fb in FALLBACK_DAYS:
                configs.append({"type": "combined", "half_exit_pct": hf,
                                "full_exit_pct": ff, "stop_profit_days": sd,
                                "fallback_days": fb})
    return configs


def run_phase2(
    greeds: np.ndarray, closes: np.ndarray, pct: np.ndarray,
    zscore: np.ndarray, dates: List[str], min_history: int, name: str,
    top_entries: List[Dict],
    exec_delay: int = 1,
) -> List[Dict]:
    """Test exit strategies against top entry configs."""
    exit_cfgs = build_exit_configs()
    results = []
    for entry_info in top_entries:
        entry = {k: entry_info[k] for k in ("threshold_type", "threshold_value",
                 "turning_days", "dca_strategy", "position_weighting")}
        for exit_cfg in exit_cfgs:
            r = simulate_dca(greeds, closes, pct, zscore, dates, entry, exit_cfg, min_history, exec_delay)
            results.append({
                **entry,
                "exit_config": exit_cfg,
                **{k: v for k, v in r.items() if k != "details"},
                "_d": len(r.get("details", [])),
            })
    results.sort(key=lambda x: (x["trades"] >= MIN_TRADES, x["adj_cagr"]), reverse=True)
    return results


# ═══════════════════════════════════════════════════════════════════════
# PHASE 3: VALIDATION
# ═══════════════════════════════════════════════════════════════════════

def walk_forward(
    greeds: np.ndarray, closes: np.ndarray, pct: np.ndarray,
    zscore: np.ndarray, dates: List[str], min_history: int,
    entry: Dict, exit_cfg: Dict, train_ratio: float = 0.70,
    exec_delay: int = 1,
) -> Dict:
    split = int(len(greeds) * train_ratio)

    def subset(arr, start, end):
        return arr[start:end].copy()

    offset = max(0, split - min_history)
    train_r = simulate_dca(
        subset(greeds, 0, split), subset(closes, 0, split),
        subset(pct, 0, split), subset(zscore, 0, split),
        dates[:split], entry, exit_cfg, min_history, exec_delay,
    )
    test_r = simulate_dca(
        subset(greeds, offset, len(greeds)), subset(closes, offset, len(greeds)),
        subset(pct, offset, len(greeds)), subset(zscore, offset, len(greeds)),
        dates[offset:], entry, exit_cfg, min_history, exec_delay,
    )

    train_c, test_c = train_r["cagr"], test_r["cagr"]
    decay = round((train_c - test_c) / max(abs(train_c), 0.01), 4) if train_r["trades"] >= 3 and test_r["trades"] >= 3 else None

    return {
        "train": {k: v for k, v in train_r.items() if k != "details"},
        "test": {k: v for k, v in test_r.items() if k != "details"},
        "cagr_decay": decay,
    }


def yearly_breakdown(
    greeds: np.ndarray, closes: np.ndarray, pct: np.ndarray,
    zscore: np.ndarray, dates: List[str], min_history: int,
    entry: Dict, exit_cfg: Dict,
    exec_delay: int = 1,
) -> Dict:
    r = simulate_dca(greeds, closes, pct, zscore, dates, entry, exit_cfg, min_history, exec_delay)
    trades = r.get("details", [])
    yearly = defaultdict(list)
    for t in trades:
        if t["entry_date_idx"] < len(dates):
            year = dates[t["entry_date_idx"]][:4]
            yearly[year].append(t["return"])
    summary = {}
    for year, rets in sorted(yearly.items()):
        if rets:
            summary[year] = {
                "n": len(rets),
                "avg_return": round(float(np.mean(rets)), 4),
                "win_rate": round(float(np.sum(np.array(rets) > 0) / len(rets)), 4),
                "total_return": round(float(np.sum(rets)), 4),
            }
    return summary


def sensitivity_analysis(
    greeds: np.ndarray, closes: np.ndarray, pct: np.ndarray,
    zscore: np.ndarray, dates: List[str], min_history: int,
    base_entry: Dict, base_exit: Dict,
    exec_delay: int = 1,
) -> Dict:
    base_r = simulate_dca(greeds, closes, pct, zscore, dates, base_entry, base_exit, min_history, exec_delay)
    base_cagr = max(base_r["cagr"], 0.005)

    cagrs = [base_cagr]
    labels = {}

    # Vary threshold ±30%
    tv = base_entry["threshold_value"]
    for dp in [-0.3, -0.2, -0.1, 0.1, 0.2, 0.3]:
        new_tv = tv * (1 + dp)
        if base_entry["threshold_type"] == "pct":
            new_tv = max(3, min(30, new_tv))
        elif base_entry["threshold_type"] == "zscore":
            new_tv = max(-4.0, min(-0.3, new_tv))
        else:
            new_tv = max(0.05, min(0.8, new_tv))
        e = {**base_entry, "threshold_value": round(new_tv, 3)}
        r = simulate_dca(greeds, closes, pct, zscore, dates, e, base_exit, min_history, exec_delay)
        cagrs.append(r["cagr"])
        labels[f"thresh_{dp:+.0%}"] = r["cagr"]

    # Vary turning days
    for td in [0, 1, 2, 3]:
        if td == base_entry["turning_days"]:
            continue
        e = {**base_entry, "turning_days": td}
        r = simulate_dca(greeds, closes, pct, zscore, dates, e, base_exit, min_history, exec_delay)
        cagrs.append(r["cagr"])
        labels[f"turning={td}d"] = r["cagr"]

    # Vary half_exit / full_exit
    for key in ["half_exit_pct", "full_exit_pct"]:
        if key in base_exit:
            for delta in [-10, 10]:
                new_val = base_exit[key] + delta
                if 15 <= new_val <= 90:
                    ex = {**base_exit, key: new_val}
                    r = simulate_dca(greeds, closes, pct, zscore, dates, base_entry, ex, min_history, exec_delay)
                    cagrs.append(r["cagr"])
                    labels[f"exit_{key}={new_val}"] = r["cagr"]

    # Vary fallback
    if "fallback_days" in base_exit:
        for delta in [-15, -5, 5, 15]:
            nfb = base_exit["fallback_days"] + delta
            if 10 <= nfb <= 90:
                ex = {**base_exit, "fallback_days": nfb}
                r = simulate_dca(greeds, closes, pct, zscore, dates, base_entry, ex, min_history, exec_delay)
                cagrs.append(r["cagr"])
                labels[f"fallback={nfb}d"] = r["cagr"]

    cagr_arr = np.array(cagrs)
    stability = round(float(1.0 / (1.0 + np.std(cagr_arr))), 4) if len(cagrs) >= 2 else 0

    return {
        "base_cagr": round(base_cagr, 4),
        "sensitivity": {k: round(v, 4) for k, v in labels.items()},
        "stability_score": stability,
        "cagr_range": (round(float(np.min(cagr_arr)), 4), round(float(np.max(cagr_arr)), 4)),
    }


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def pct_str(v: float) -> str:
    return f"{v*100:+.1f}%"


def run(exec_delay: int = 1):
    """Run full backtest with configurable execution delay.

    exec_delay=0: idealized same-day execution (buy from signal confirmation day).
    exec_delay=1: realistic T+1 execution (default, matches production data lag).
    """
    api_key = read_api_key()
    print("=" * 120)
    print("  黄金坑终极回测 v2 — DCA 定投全维度优化 (修正 CAGR)")
    print("  规则: 日历跨度 CAGR  |  最低交易 {MIN_TRADES} 笔  |  频率加权评分  |  执行延迟: {delay}天".format(
        MIN_TRADES=MIN_TRADES, delay=exec_delay))
    print("=" * 120)

    # ── Fetch ──
    print("\n[1/5] 获取全量数据...")
    all_data = fetch_full_series(api_key)
    print(f"获取到 {len(all_data)} 个指数\n")

    index_data = {}
    for code, cfg in INDICES.items():
        raw = all_data.get(code, [])
        if not raw:
            continue
        series = sorted(raw, key=lambda x: x.get("date", ""))
        g = np.array([float(s.get("greed", 0)) for s in series])
        c = np.array([float(s.get("close", 0)) for s in series])
        dts = [s.get("date", "") for s in series]
        if len(g) < cfg["min_history"] + 30:
            continue
        pc = compute_rolling_percentile(g)
        zs = compute_zscore(g)
        index_data[code] = {
            "name": cfg["name"], "market": cfg["market"],
            "min_history": cfg["min_history"],
            "greeds": g, "closes": c, "pct": pc, "zscore": zs, "dates": dts,
        }
        n_d = len(g)
        print(f"  {cfg['name']:<8s} ({code}) {dts[0]}~{dts[-1]} ({n_d}天)  "
              f"greed=[{g.min():.3f}, {g.max():.3f}]")

    # ── Phase 1 ──
    print(f"\n{'=' * 120}")
    print(f"  Phase 1: 入场参数穷举 (固定持有30天, 最低{MIN_TRADES}笔)")
    print(f"  搜索: 阈值类型(fixed/pct/zscore) × 确认(0-3d) × DCA策略(9) × 仓位加权(3)")
    print(f"{'=' * 120}")

    p1_top = {}

    for code, data in index_data.items():
        name = data["name"]
        n_thresh = len(build_fixed_thresholds(data["greeds"])) + len(PCT_THRESHOLDS) + len(ZSCORE_THRESHOLDS)
        n_combos = n_thresh * len(TURNING_DAYS) * len(DCA_STRATEGIES) * len(POSITION_WEIGHTINGS)
        print(f"\n  {name} ({code}): {n_combos} 组合...")

        t0 = time.time()
        results = run_phase1(
            data["greeds"], data["closes"], data["pct"], data["zscore"],
            data["dates"], data["min_history"], name, exec_delay,
        )
        elapsed = time.time() - t0

        # Separate: qualified (≥MIN_TRADES) vs informational (< MIN_TRADES)
        qualified = [r for r in results if r["trades"] >= MIN_TRADES]
        info = [r for r in results if 3 <= r["trades"] < MIN_TRADES]

        p1_top[code] = {
            "qualified": qualified[:5],
            "info_only": info[:3],
        }

        print(f"    ({elapsed:.1f}s) 合格(≥{MIN_TRADES}笔): {len(qualified)}  信息(3-{MIN_TRADES-1}笔): {len(info)}")

        if qualified:
            best = qualified[0]
            print(f"    ★ 最优入场: {best['threshold_type']}={best['threshold_value']}  "
                  f"turning={best['turning_days']}d  {best['dca_strategy']}  {best['position_weighting']}")
            print(f"       adjCAGR={pct_str(best['adj_cagr'])} (raw={pct_str(best['cagr'])})  "
                  f"Win={best['win_rate']:.0%}  Trades={best['trades']}  "
                  f"Tr/yr={best['trades_per_year']}  MaxConc={best['max_concurrent']}  "
                  f"Score={best['score']:.3f}")
            for rank, r in enumerate(qualified[:3], 1):
                print(f"       #{rank}: {r['threshold_type']}={r['threshold_value']}  "
                      f"t{r['turning_days']}d  {r['dca_strategy']}  {r['position_weighting']}  "
                      f"CAGR={pct_str(r['cagr'])}  Win={r['win_rate']:.0%}  Tr={r['trades']}")

        if info and not qualified:
            best_info = info[0]
            print(f"    ⚠ 无合格组合。最优信息: {best_info['threshold_type']}={best_info['threshold_value']}  "
                  f"CAGR={pct_str(best_info['cagr'])}  Trades={best_info['trades']}  (频率不足)")

    # ── Phase 2 ──
    print(f"\n{'=' * 120}")
    print(f"  Phase 2: 出场策略穷举 (最低{MIN_TRADES}笔)")
    print(f"  用 Phase 1 最优入场 × {len(build_exit_configs())} 退出策略")
    print(f"{'=' * 120}")

    p2_top = {}

    for code, data in index_data.items():
        name = data["name"]
        top_info = p1_top.get(code, {})
        # Use qualified entries, fall back to info_only
        entries_to_test = top_info.get("qualified", top_info.get("info_only", []))[:5]
        if not entries_to_test:
            continue

        n_combos = len(entries_to_test) * len(build_exit_configs())
        print(f"\n  {name} ({code}): {n_combos} 组合...")

        t0 = time.time()
        results = run_phase2(
            data["greeds"], data["closes"], data["pct"], data["zscore"],
            data["dates"], data["min_history"], name, entries_to_test, exec_delay,
        )
        elapsed = time.time() - t0

        qualified = [r for r in results if r["trades"] >= MIN_TRADES]
        info = [r for r in results if 3 <= r["trades"] < MIN_TRADES]
        p2_top[code] = {"qualified": qualified[:10], "info_only": info[:5]}

        print(f"    ({elapsed:.1f}s) 合格: {len(qualified)}  信息: {len(info)}")

        if qualified:
            best = qualified[0]
            et = best["exit_config"]["type"]
            ed = {k: v for k, v in best["exit_config"].items() if k != "type"}
            ed_s = ", ".join(f"{k}={v}" for k, v in ed.items())
            print(f"    ★ Top1: adjCAGR={pct_str(best['adj_cagr'])} (raw={pct_str(best['cagr'])})  "
                  f"Win={best['win_rate']:.0%}  Trades={best['trades']}  "
                  f"Tr/yr={best['trades_per_year']}  MaxConc={best['max_concurrent']}")
            print(f"       入场: {best['threshold_type']}={best['threshold_value']}  "
                  f"t{best['turning_days']}d  {best['dca_strategy']}  {best['position_weighting']}")
            print(f"       退出: {et} ({ed_s})")
            print(f"\n    {'#':<4s} {'adjCAGR':>8s} {'Win':>6s} {'Tr':>4s} {'Tr/yr':>6s} "
                  f"{'退出类型':<16s} {'参数'}")
            for rank, r in enumerate(qualified[:5], 1):
                et = r["exit_config"]["type"]
                ed_s = ", ".join(f"{k}={v}" for k, v in r["exit_config"].items() if k != "type")
                ed_s = ed_s[:60]
                print(f"    {rank:<4d} {pct_str(r['adj_cagr']):>8s} {r['win_rate']:>5.0%} "
                      f"{r['trades']:>4d} {r['trades_per_year']:>5.1f} {et:<16s} {ed_s}")
        elif info:
            best_info = info[0]
            print(f"    ⚠ 无合格组合。最优信息: CAGR={pct_str(best_info['cagr'])}  Trades={best_info['trades']}")

    # ── Phase 3 ──
    print(f"\n{'=' * 120}")
    print(f"  Phase 3: Walk-forward + 逐年拆解 + 敏感度")
    print(f"{'=' * 120}")

    final = {"meta": {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                       "min_trades": MIN_TRADES, "exec_delay": exec_delay}, "indices": {}}

    for code, data in index_data.items():
        name = data["name"]
        top_info = p2_top.get(code, {})
        best_list = top_info.get("qualified", top_info.get("info_only", []))
        if not best_list:
            continue

        best = best_list[0]
        entry = {k: best[k] for k in ("threshold_type", "threshold_value",
                  "turning_days", "dca_strategy", "position_weighting")}
        exit_cfg = best["exit_config"]

        print(f"\n  ── {name} ({code}) ──")

        # Walk-forward
        wf = walk_forward(data["greeds"], data["closes"], data["pct"], data["zscore"],
                          data["dates"], data["min_history"], entry, exit_cfg, exec_delay=exec_delay)
        print(f"    Walk-forward:")
        print(f"      训练: CAGR={pct_str(wf['train']['cagr'])}  Win={wf['train']['win_rate']:.0%}  "
              f"Tr={wf['train']['trades']}  Tr/yr={wf['train']['trades_per_year']}")
        print(f"      测试: CAGR={pct_str(wf['test']['cagr'])}  Win={wf['test']['win_rate']:.0%}  "
              f"Tr={wf['test']['trades']}  Tr/yr={wf['test']['trades_per_year']}")
        if wf["cagr_decay"] is not None:
            abs_decay = abs(wf["cagr_decay"])
            if abs_decay < 0.3:
                label = "优秀"
            elif abs_decay < 0.5:
                label = "可接受"
            elif abs_decay < 1.0:
                label = "注意"
            else:
                label = "过拟合风险"
            print(f"      衰减: {wf['cagr_decay']:.1%} → {label}")

        # Yearly
        yearly = yearly_breakdown(data["greeds"], data["closes"], data["pct"], data["zscore"],
                                  data["dates"], data["min_history"], entry, exit_cfg, exec_delay)
        print(f"    逐年:")
        for year, ys in yearly.items():
            print(f"      {year}: {ys['n']}笔  Avg={pct_str(ys['avg_return'])}  "
                  f"Win={ys['win_rate']:.0%}  Total={pct_str(ys['total_return'])}")

        # Sensitivity
        sens = sensitivity_analysis(data["greeds"], data["closes"], data["pct"], data["zscore"],
                                    data["dates"], data["min_history"], entry, exit_cfg, exec_delay)
        print(f"    敏感度: Stability={sens['stability_score']:.2f}  "
              f"CAGR∈[{pct_str(sens['cagr_range'][0])}, {pct_str(sens['cagr_range'][1])}]")
        if sens["stability_score"] >= 0.7:
            print(f"      → 参数稳健 ✓")
        elif sens["stability_score"] >= 0.5:
            print(f"      → 较稳健")
        else:
            print(f"      → 参数敏感，注意过拟合")

        final["indices"][code] = {
            "name": name, "market": data["market"],
            "n_days": len(data["greeds"]),
            "date_range": f"{data['dates'][0]}~{data['dates'][-1]}",
            "top1": {
                "cagr": best["cagr"], "adj_cagr": best["adj_cagr"],
                "win_rate": best["win_rate"],
                "trades": best["trades"], "trades_per_year": best["trades_per_year"],
                "capital_efficiency": best["capital_efficiency"],
                "max_concurrent": best["max_concurrent"],
                "sharpe": best["sharpe"], "max_drawdown": best["max_drawdown"],
                "profit_factor": best["profit_factor"],
                "entry": entry, "exit": exit_cfg,
            },
            "top5": [{
                "cagr": r["cagr"], "win_rate": r["win_rate"],
                "trades": r["trades"], "trades_per_year": r["trades_per_year"],
                "entry": {k: r[k] for k in ("threshold_type", "threshold_value",
                          "turning_days", "dca_strategy", "position_weighting")},
                "exit": r["exit_config"],
                "capital_efficiency": r["capital_efficiency"],
            } for r in best_list[:5]],
            "walk_forward": {
                "train_cagr": wf["train"]["cagr"], "test_cagr": wf["test"]["cagr"],
                "cagr_decay": wf["cagr_decay"],
            },
            "yearly": yearly,
            "sensitivity": {
                "stability_score": sens["stability_score"],
                "cagr_range": sens["cagr_range"],
            },
        }

    # ── Cross-index patterns ──
    print(f"\n{'=' * 120}")
    print(f"  跨指数规律")
    print(f"{'=' * 120}")

    entries = [d["top1"]["entry"] for d in final["indices"].values()]
    exit_types = Counter(d["top1"]["exit"]["type"] for d in final["indices"].values())

    print(f"\n  最优入场: threshold_type={dict(Counter(e['threshold_type'] for e in entries))}")
    print(f"  拐点确认: {dict(Counter(e['turning_days'] for e in entries))}")
    print(f"  DCA策略: {dict(Counter(e['dca_strategy'] for e in entries))}")
    print(f"  仓位加权: {dict(Counter(e['position_weighting'] for e in entries))}")
    print(f"  最优退出: {dict(exit_types)}")

    # Exit params
    staged_half = [d["top1"]["exit"]["half_exit_pct"] for d in final["indices"].values()
                   if "half_exit_pct" in d["top1"]["exit"]]
    staged_full = [d["top1"]["exit"]["full_exit_pct"] for d in final["indices"].values()
                   if "full_exit_pct" in d["top1"]["exit"]]
    fallbacks = [d["top1"]["exit"].get("fallback_days", 0) for d in final["indices"].values()
                 if "fallback_days" in d["top1"]["exit"]]

    if staged_half:
        print(f"  Half退出: mean={np.mean(staged_half):.0f}  range=[{min(staged_half)}-{max(staged_half)}]")
    if staged_full:
        print(f"  Full退出: mean={np.mean(staged_full):.0f}  range=[{min(staged_full)}-{max(staged_full)}]")
    if fallbacks:
        print(f"  兜底天数: mean={np.mean(fallbacks):.0f}d  range=[{min(fallbacks)}-{max(fallbacks)}]")

    # Portfolio summary
    print(f"\n{'=' * 120}")
    print(f"  等权组合")
    print(f"{'=' * 120}")

    cagrs = []
    for code, d in final["indices"].items():
        c = d["top1"]["adj_cagr"]
        c_raw = d["top1"]["cagr"]
        wr = d["top1"]["win_rate"]
        tr = d["top1"]["trades"]
        tyr = d["top1"]["trades_per_year"]
        mc = d["top1"]["max_concurrent"]
        print(f"  {d['name']:<8s}  adjCAGR={pct_str(c)} (raw={pct_str(c_raw)})  "
              f"Win={wr:.0%}  Tr={tr}  Tr/yr={tyr:.1f}  MaxConc={mc}")
        cagrs.append(c)

    if cagrs:
        avg_c = float(np.mean(cagrs))
        print(f"\n  等权 adjCAGR: {pct_str(avg_c)}")
        print(f"  最差单指数: {pct_str(min(cagrs))}")

    final["cross_index"] = {
        "entry_patterns": {
            "threshold_types": dict(Counter(e["threshold_type"] for e in entries)),
            "turning_days": dict(Counter(e["turning_days"] for e in entries)),
            "dca_strategies": dict(Counter(e["dca_strategy"] for e in entries)),
            "position_weightings": dict(Counter(e["position_weighting"] for e in entries)),
        },
        "exit_patterns": dict(exit_types),
        "exit_params": {
            "half_pcts": {"mean": float(np.mean(staged_half)) if staged_half else None},
            "full_pcts": {"mean": float(np.mean(staged_full)) if staged_full else None},
            "fallback_days": {"mean": float(np.mean(fallbacks)) if fallbacks else None},
        },
        "portfolio_equal_weight_cagr": round(float(avg_c), 4) if cagrs else 0,
    }

    # ── Save ──
    def make_serializable(obj):
        if isinstance(obj, dict):
            return {k: make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [make_serializable(v) for v in obj]
        elif isinstance(obj, (np.integer,)):
            return int(obj)
        elif isinstance(obj, (np.floating,)):
            return float(obj)
        elif isinstance(obj, (np.bool_,)):
            return bool(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return obj

    out_path = PROJECT_ROOT / "golden_pit_ultimate_report.json"
    out_path.write_text(
        json.dumps(make_serializable(final), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n  报告: {out_path}")
    print(f"\n{'=' * 120}")
    print(f"  回测完成。")
    print(f"{'=' * 120}")


if __name__ == "__main__":
    run()
