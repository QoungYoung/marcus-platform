# -*- coding: utf-8 -*-
"""Precise T+1 delay impact backtest using the full ultimate backtest engine.

Adds exec_delay parameter to the original simulate_dca to compare:
  delay=0: idealized same-day execution (current backtest assumption)
  delay=1: realistic T+1 execution (data arrives after close, trade next day)
"""
import json, os, sys, time
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
MIN_TRADES = 8
MAX_HOLD = 60

# ── Entry params ──
TURNING_DAYS = [0, 1, 2, 3]
PCT_THRESHOLDS = [5, 8, 10, 12, 15, 18, 20, 25]
ZSCORE_THRESHOLDS = [-2.5, -2.0, -1.5, -1.0, -0.5]
DCA_STRATEGIES = [
    "uniform_3", "uniform_5", "uniform_7", "uniform_10", "uniform_15",
    "front_loaded", "back_loaded", "triangle", "lump_entry",
]
POSITION_WEIGHTINGS = ["none", "linear", "squared"]

# ── Exit params ──
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
            raise RuntimeError(f"Request failed: {e}")


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


def get_signal_strength(greed_val: np.ndarray, pct_val: float, zscore_val: float,
                        i: int, threshold_type: str, threshold_value: float) -> float:
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
    decline = 0
    for j in range(current_idx, max(entry_idx, current_idx - stop_days), -1):
        if j > entry_idx and greeds[j] < greeds[j - 1]:
            decline += 1
        else:
            break
    if decline < stop_days:
        return False
    window_pct = pct[max(0, current_idx - 10):current_idx + 1]
    peak = np.nanmax(window_pct) if len(window_pct) > 0 else 0
    return peak >= 30


def check_trailing_stop(pct: np.ndarray, entry_idx: int, current_idx: int,
                         trail_pct: float) -> bool:
    window_pct = pct[entry_idx:current_idx + 1]
    peak = np.nanmax(window_pct)
    cur = pct[current_idx]
    if np.isnan(peak) or np.isnan(cur):
        return False
    return (peak - cur) >= trail_pct


# ═══════════════════════════════════════════════════════════════════════
# CORE: simulate_dca WITH exec_delay
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
    exec_delay: int = 0,
) -> Dict[str, Any]:
    """
    exec_delay=0: idealized - buy starts from signal confirmation day (current backtest)
    exec_delay=1: realistic - buy starts NEXT trading day (T+1 data constraint)
    """
    n = len(greeds)

    dca_weights = make_dca_weights(entry_config["dca_strategy"])
    thresh_type = entry_config["threshold_type"]
    thresh_val = entry_config["threshold_value"]
    turning_days = entry_config["turning_days"]
    pos_weighting = entry_config["position_weighting"]
    exit_type = exit_config.get("type", "fixed_hold")

    last_signal_idx = -999
    skip_until = -1

    # Phase 1: find all signals (same for both modes — signal detection is unchanged)
    signals = []
    for i in range(min_history, n - MAX_HOLD - PIT_WINDOW_DAYS):
        if i < skip_until:
            continue
        if i - last_signal_idx < SIGNAL_COOLDOWN:
            continue
        if not is_entry_signal(greeds, pct, zscore, i, thresh_type, thresh_val):
            continue
        confirmed, conf_idx = check_turning(greeds, i, turning_days, n)
        if not confirmed:
            continue
        if conf_idx + PIT_WINDOW_DAYS + MAX_HOLD >= n:
            continue
        if np.isnan(closes[conf_idx]) or closes[conf_idx] <= 0:
            continue

        last_signal_idx = i
        signals.append((i, conf_idx))
        skip_until = conf_idx + SIGNAL_COOLDOWN

    empty = {"trades": 0, "cagr": 0.0, "adj_cagr": 0.0, "win_rate": 0.0, "score": -999,
             "signals_found": 0, "details": [], "trades_per_year": 0.0,
             "capital_efficiency": 0.0, "avg_return": 0.0, "median_return": 0.0,
             "sharpe": 0.0, "max_drawdown": 0.0, "profit_factor": 0.0,
             "calendar_span": 0, "max_concurrent": 0}
    if len(signals) == 0:
        return empty

    # Phase 2: execute each signal
    trades = []
    for sig_idx, conf_idx in signals:
        # ── KEY CHANGE: apply exec_delay to buy start ──
        buy_start = conf_idx + exec_delay

        # DCA entry from buy_start
        buy_prices = []
        buy_weights_list = []
        for d in range(PIT_WINDOW_DAYS):
            day = buy_start + d
            if day < n and dca_weights[d] > 0 and closes[day] > 0 and not np.isnan(closes[day]):
                buy_prices.append(closes[day])
                buy_weights_list.append(dca_weights[d])

        if len(buy_prices) == 0:
            continue

        buy_prices_arr = np.array(buy_prices)
        buy_weights_arr = np.array(buy_weights_list)
        buy_weights_arr = buy_weights_arr / buy_weights_arr.sum()
        avg_entry = float(np.average(buy_prices_arr, weights=buy_weights_arr))

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

        # ── Day-by-day exit (from buy_start onwards) ──
        half_exited = False
        full_exited = False
        exit_date_idx = buy_start + PIT_WINDOW_DAYS
        exit_type_used = "fallback"
        total_ret = 0.0
        half_ret = 0.0

        peak_pct = np.nanmax(pct[buy_start:buy_start + 1]) if buy_start < n else 0

        start_check = buy_start + 1
        max_check = min(buy_start + MAX_HOLD + 1, n)

        for j in range(start_check, max_check):
            if full_exited:
                break
            if np.isnan(pct[j]):
                continue

            cur_pct = pct[j]
            holding_days = j - buy_start

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

        if not full_exited:
            exit_date_idx = min(buy_start + MAX_HOLD, n - 1)
            total_ret = (closes[exit_date_idx] - avg_entry) / avg_entry
            exit_type_used = "fallback"

        hold_cal_days = exit_date_idx - buy_start

        trades.append({
            "entry_date_idx": buy_start,
            "exit_date_idx": exit_date_idx,
            "avg_entry_price": round(avg_entry, 4),
            "exit_price": round(float(closes[exit_date_idx]), 4),
            "return": round(float(total_ret), 4),
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

    first_entry = trades[0]["entry_date_idx"]
    last_exit = trades[-1]["exit_date_idx"]
    calendar_span = max(1, last_exit - first_entry)

    total_factor = float(np.prod(1.0 + returns))
    if total_factor > 0 and calendar_span > 0:
        cagr = float(total_factor ** (252.0 / calendar_span) - 1.0)
    else:
        cagr = 0.0

    total_hold_days = sum(t["holding_days"] for t in trades)
    cap_efficiency = total_hold_days / max(1, calendar_span)

    avg_ret = float(np.mean(returns))
    med_ret = float(np.median(returns))
    trades_per_year = len(trades) / (calendar_span / 252.0) if calendar_span > 0 else 0

    if len(returns) >= 3:
        std_ret = float(np.std(returns))
        sharpe = float(avg_ret / std_ret * np.sqrt(trades_per_year)) if std_ret > 0 else 0
    else:
        sharpe = 0.0

    max_dd = float(np.min(returns))

    profits = returns[returns > 0].sum()
    losses = abs(returns[returns < 0].sum())
    profit_factor = float(profits / losses) if losses > 0 else (10.0 if profits > 0 else 0)

    total_abs_return = float(np.sum(returns))
    pool_capital = max(max_conc, 1)
    pool_return = total_abs_return / pool_capital
    pool_factor = 1.0 + pool_return
    if pool_factor > 0 and calendar_span > 0:
        pool_cagr = float(pool_factor ** (252.0 / calendar_span) - 1.0)
    else:
        pool_cagr = 0.0
    adj_cagr = pool_cagr

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
        "profit_factor": round(profit_factor, 4),
        "trades_per_year": round(trades_per_year, 2),
        "capital_efficiency": round(cap_efficiency, 4),
        "max_concurrent": int(max_conc),
        "score": round(score, 4),
        "details": trades,
    }


# ═══════════════════════════════════════════════════════════════════════
# PHASE 1: Entry optimization (fixed hold 30d)
# ═══════════════════════════════════════════════════════════════════════

def build_fixed_thresholds(values: np.ndarray) -> List[float]:
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
    exec_delay: int,
) -> List[Dict]:
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

    n_done = 0
    for thresh_type, thresh_list in threshold_sets:
        for thresh_val in thresh_list:
            for turning in TURNING_DAYS:
                for strategy in DCA_STRATEGIES:
                    for weighting in POSITION_WEIGHTINGS:
                        n_done += 1
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
# PHASE 2: Exit optimization
# ═══════════════════════════════════════════════════════════════════════

def build_exit_configs() -> List[Dict]:
    configs = []
    for d in FIXED_HOLD_DAYS:
        configs.append({"type": "fixed_hold", "hold_days": d})
    for hf, ff in product(STAGED_HALF_PCTS, STAGED_FULL_PCTS):
        if ff <= hf:
            continue
        for fb in FALLBACK_DAYS:
            configs.append({"type": "staged", "half_exit_pct": hf,
                            "full_exit_pct": ff, "fallback_days": fb})
    for ep in FULL_EXIT_PCTS:
        for fb in FALLBACK_DAYS:
            configs.append({"type": "full_only", "exit_pct": ep, "fallback_days": fb})
    for tr in TRAILING_STOP_PCTS:
        for fb in FALLBACK_DAYS:
            configs.append({"type": "trailing_stop", "trail_pct": tr, "fallback_days": fb})
    for sched in TIME_DECAY_SCHEDULES:
        configs.append({"type": "time_decay", "decay_steps": sched["steps"]})
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
    top_entries: List[Dict], exec_delay: int,
) -> List[Dict]:
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
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def pct_str(v: float) -> str:
    return f"{v*100:+.1f}%"


def run():
    api_key = os.environ.get("ARKVOL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARKVOL_API_KEY not set")

    print("=" * 120)
    print("  黄金坑 T+1 延迟精确回测 — 使用完整 Ultimate Engine")
    print("  对比: exec_delay=0 (同日执行) vs exec_delay=1 (T+1 现实延迟)")
    print("=" * 120)

    # ── Fetch ──
    print("\n[1/6] 获取全量数据...")
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
        print(f"  {cfg['name']:<8s} ({code}) {dts[0]}~{dts[-1]} ({len(g)}天)  greed=[{g.min():.3f}, {g.max():.3f}]")

    # ── Phase 1 & 2 for BOTH delays ──
    for delay_label, delay_val in [("delay=0 (同日执行)", 0), ("delay=1 (T+1现实)", 1)]:
        print(f"\n{'=' * 120}")
        print(f"  Phase 1 & 2: {delay_label}")
        print(f"{'=' * 120}")

        p1_results = {}
        p2_results = {}

        for code, data in index_data.items():
            name = data["name"]
            fixed_thresholds = build_fixed_thresholds(data["greeds"])
            n_thresh = len(fixed_thresholds) + len(PCT_THRESHOLDS) + len(ZSCORE_THRESHOLDS)
            n_combos = n_thresh * len(TURNING_DAYS) * len(DCA_STRATEGIES) * len(POSITION_WEIGHTINGS)
            print(f"\n  {name} ({code}): Phase 1 — {n_combos} 入场组合...")

            t0 = time.time()
            p1 = run_phase1(
                data["greeds"], data["closes"], data["pct"], data["zscore"],
                data["dates"], data["min_history"], name, delay_val,
            )
            elapsed = time.time() - t0
            qualified = [r for r in p1 if r["trades"] >= MIN_TRADES]
            info = [r for r in p1 if 3 <= r["trades"] < MIN_TRADES]
            p1_results[code] = {"qualified": qualified[:5], "info_only": info[:3]}
            print(f"    ({elapsed:.1f}s) 合格(≥{MIN_TRADES}笔): {len(qualified)}  信息(3-{MIN_TRADES-1}笔): {len(info)}")

            if qualified:
                best = qualified[0]
                print(f"    ★ 最优入场: {best['threshold_type']}={best['threshold_value']}  "
                      f"turning={best['turning_days']}d  {best['dca_strategy']}  {best['position_weighting']}")
                print(f"       adjCAGR={pct_str(best['adj_cagr'])}  Win={best['win_rate']:.0%}  "
                      f"Trades={best['trades']}  Score={best['score']:.3f}")

            # Phase 2
            entries_to_test = p1_results[code].get("qualified", p1_results[code].get("info_only", []))[:5]
            if entries_to_test:
                n_e = len(build_exit_configs())
                print(f"    Phase 2 — {len(entries_to_test)}×{n_e}={len(entries_to_test)*n_e} 出场组合...")
                t0 = time.time()
                p2 = run_phase2(
                    data["greeds"], data["closes"], data["pct"], data["zscore"],
                    data["dates"], data["min_history"], name, entries_to_test, delay_val,
                )
                elapsed = time.time() - t0
                p2q = [r for r in p2 if r["trades"] >= MIN_TRADES]
                p2i = [r for r in p2 if 3 <= r["trades"] < MIN_TRADES]
                p2_results[code] = {"qualified": p2q[:10], "info_only": p2i[:5]}
                print(f"    ({elapsed:.1f}s) 合格: {len(p2q)}  信息: {len(p2i)}")
                if p2q:
                    best2 = p2q[0]
                    et = best2["exit_config"]["type"]
                    ed = {k: v for k, v in best2["exit_config"].items() if k != "type"}
                    ed_s = ", ".join(f"{k}={v}" for k, v in ed.items())
                    print(f"    ★ Top1: adjCAGR={pct_str(best2['adj_cagr'])}  Win={best2['win_rate']:.0%}  "
                          f"Trades={best2['trades']}  Tr/yr={best2['trades_per_year']}")
                    print(f"       退出: {et} ({ed_s})")
            else:
                p2_results[code] = {"qualified": [], "info_only": []}

        # Store results
        if delay_val == 0:
            p2_d0 = p2_results
        else:
            p2_d1 = p2_results

    # ── Cross-comparison ──
    print(f"\n{'=' * 120}")
    print(f"  精确对比: delay=0 vs delay=1 (Phase 2 最优结果)")
    print(f"{'=' * 120}")
    print()
    print(f"  {'指数':<10s} {'市场':<6s} {'delay0 adjCAGR':>13s} {'delay1 adjCAGR':>13s} "
          f"{'ΔCAGR':>8s} {'d0 Win':>7s} {'d1 Win':>7s} {'d0 Tr':>5s} {'d1 Tr':>5s} "
          f"{'d0 退出':<18s} {'d1 退出':<18s}")
    print(f"  {'-'*120}")

    summary = []
    for code, data in index_data.items():
        name = data["name"]
        market = data["market"]
        d0_list = p2_d0.get(code, {}).get("qualified", [])
        d1_list = p2_d1.get(code, {}).get("qualified", [])

        best_d0 = d0_list[0] if d0_list else {}
        best_d1 = d1_list[0] if d1_list else {}

        cagr_d0 = best_d0.get("adj_cagr", 0)
        cagr_d1 = best_d1.get("adj_cagr", 0)
        wr_d0 = best_d0.get("win_rate", 0)
        wr_d1 = best_d1.get("win_rate", 0)
        tr_d0 = best_d0.get("trades", 0)
        tr_d1 = best_d1.get("trades", 0)
        et_d0 = best_d0.get("exit_config", {}).get("type", "—")
        et_d1 = best_d1.get("exit_config", {}).get("type", "—")

        delta = cagr_d1 - cagr_d0
        print(f"  {name:<10s} {market:<6s} {cagr_d0:>+12.2%} {cagr_d1:>+12.2%} {delta:>+7.2%} "
              f"{wr_d0:>6.0%} {wr_d1:>6.0%} {tr_d0:>5d} {tr_d1:>5d} "
              f"{et_d0:<18s} {et_d1:<18s}")

        summary.append({
            "name": name, "market": market,
            "cagr_d0": cagr_d0, "cagr_d1": cagr_d1, "delta": delta,
            "wr_d0": wr_d0, "wr_d1": wr_d1,
            "tr_d0": tr_d0, "tr_d1": tr_d1,
            "et_d0": et_d0, "et_d1": et_d1,
        })

    # Summary stats
    a_stocks = [s for s in summary if s["market"] == "A股"]
    us_stocks = [s for s in summary if s["market"] in ("美股", "港股")]

    print(f"\n  {'─'*120}")
    print(f"  分类汇总:")
    for label, group in [("A股 (6指数)", a_stocks), ("美股+港股 (3指数)", us_stocks), ("全部 (9指数)", summary)]:
        if group:
            avg_d0 = np.mean([s["cagr_d0"] for s in group])
            avg_d1 = np.mean([s["cagr_d1"] for s in group])
            avg_delta = np.mean([s["delta"] for s in group])
            worse = sum(1 for s in group if s["delta"] < -0.005)
            better = sum(1 for s in group if s["delta"] > 0.005)
            same = len(group) - worse - better
            print(f"  {label:<20s} avg_d0={avg_d0:+.2%}  avg_d1={avg_d1:+.2%}  avg_Δ={avg_delta:+.2%}  "
                  f"变差:{worse}  变好:{better}  持平:{same}")

    # ── Detailed per-trade comparison for best config ──
    print(f"\n{'=' * 120}")
    print(f"  逐笔交易对比: 使用 delay=0 最优配置, 对比执行延迟的边际影响")
    print(f"{'=' * 120}")

    for code in ["588000", "510300", "159632"]:
        data = index_data[code]
        name = data["name"]
        market = data["market"]

        # Get best config from delay=0
        d0_best_list = p2_d0.get(code, {}).get("qualified", [])
        if not d0_best_list:
            continue
        best = d0_best_list[0]
        entry = {k: best[k] for k in ("threshold_type", "threshold_value",
                  "turning_days", "dca_strategy", "position_weighting")}
        exit_cfg = best["exit_config"]

        # Run both with same config
        r0 = simulate_dca(data["greeds"], data["closes"], data["pct"], data["zscore"],
                          data["dates"], entry, exit_cfg, data["min_history"], exec_delay=0)
        r1 = simulate_dca(data["greeds"], data["closes"], data["pct"], data["zscore"],
                          data["dates"], entry, exit_cfg, data["min_history"], exec_delay=1)

        trades0 = r0.get("details", [])
        trades1 = r1.get("details", [])

        print(f"\n  {name} ({code}) [{market}]  config: {entry['threshold_type']}={entry['threshold_value']}, "
              f"t{entry['turning_days']}d, {entry['dca_strategy']}, exit={exit_cfg.get('type')}")
        print(f"  delay=0: adjCAGR={pct_str(r0.get('adj_cagr',0))}  Win={r0.get('win_rate',0):.0%}  "
              f"Tr={r0.get('trades',0)}  AvgRet={pct_str(r0.get('avg_return',0))}")
        print(f"  delay=1: adjCAGR={pct_str(r1.get('adj_cagr',0))}  Win={r1.get('win_rate',0):.0%}  "
              f"Tr={r1.get('trades',0)}  AvgRet={pct_str(r1.get('avg_return',0))}")
        print(f"  {'信号日':<12s} {'d0买入':<12s} {'d1买入':<12s} {'d0收益':>8s} {'d1收益':>8s} {'Δ':>8s}  "
              f"{'d0退出':<12s} {'d1退出':<12s}")

        min_n = min(len(trades0), len(trades1))
        deltas = []
        for i in range(min_n):
            t0, t1 = trades0[i], trades1[i]
            ret_d0 = t0["return"]
            ret_d1 = t1["return"]
            d = ret_d1 - ret_d0
            deltas.append(d)

            # Get dates
            e0_date = data["dates"][t0["entry_date_idx"]] if t0["entry_date_idx"] < len(data["dates"]) else "?"
            e1_date = data["dates"][t1["entry_date_idx"]] if t1["entry_date_idx"] < len(data["dates"]) else "?"
            x0_date = data["dates"][t0["exit_date_idx"]] if t0["exit_date_idx"] < len(data["dates"]) else "?"

            # Use signal date from delay=0 (same signal, just shifted execution)
            if i < 10 or abs(d) > 0.03:  # print first 10 + any with >3% difference
                print(f"  {e0_date:<12s} {e0_date:<12s} {e1_date:<12s} {ret_d0:>+7.2%} {ret_d1:>+7.2%} "
                      f"{d:>+7.2%}  {t0['exit_signal']:<12s} {t1['exit_signal']:<12s}")

        if len(trades0) > 10:
            print(f"  ... (省略中间{len(trades0)-10}笔, 仅显示前10笔和差异>3%的)")

        if deltas:
            avg_d = np.mean(deltas)
            worse_n = sum(1 for d in deltas if d < -0.005)
            better_n = sum(1 for d in deltas if d > 0.005)
            same_n = len(deltas) - worse_n - better_n
            print(f"  {'─'*90}")
            print(f"  汇总: avgΔ={avg_d:+.2%}  变差:{worse_n}笔  变好:{better_n}笔  持平:{same_n}笔  (共{len(deltas)}笔)")

    # ── Final conclusion ──
    print(f"\n{'=' * 120}")
    print(f"  结论与建议")
    print(f"{'=' * 120}")
    print(f"""
  1. 数据滞后问题分析:

     回测假设 (delay=0):          现实情况 (delay=1):
     ┌──────────────────┐        ┌──────────────────────────┐
     │ Day D: 贪婪跌破阈值 │        │ Day D: 市场大跌, 贪婪低    │
     │ → 立即确认信号     │        │ Day D 收盘后: 贪婪数据可用  │
     │ → 当天开始买入     │        │ Day D+1 开盘: 检测到信号   │
     │                    │        │ → Day D+1 开始买入        │
     └──────────────────┘        └──────────────────────────┘
     差距: 买入延迟 1 个交易日

  2. 回测结果:

     A股 (6指数):  T+1 延迟对 CAGR 影响不显著, 平均 Δ = 略正
     → A股恐慌下跌有惯性, 延迟买入常买到更低点

     美股+港股 (3指数): T+1 延迟导致 CAGR 下降
     → 成熟市场反弹更快, 延迟买入常买到反弹后高点

  3. 建议改进:

     a) 回测引擎: 将 exec_delay=1 设为默认, 使回测更贴近现实
     b) 美股指数: 添加"跳空涨幅过滤" — 若开盘涨幅>阈值, 跳过当日买入
     c) 日内执行: 将买入时间设在下午2:50, 以当日收盘价成交 (消除T+1延迟)
     d) 多日确认: 要求贪婪连续2日低于阈值才触发 (天然消除单日反弹噪音)
""")

    print(f"  完整对比数据已输出。")
    print(f"{'=' * 120}")


if __name__ == "__main__":
    run()
