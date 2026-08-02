# -*- coding: utf-8 -*-
"""策略年化收益率测算: 按当前校准参数模拟完整交易周期"""

import os, sys
from pathlib import Path
import numpy as np
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import dotenv
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    dotenv.load_dotenv(env_path)

import requests

# Current calibrated params
INDICES = {
    "588000": {"name": "科创50",   "tier": "core",   "entry": 8,  "td": 1, "exit_full": 60, "exit_half": 60, "fb": 60, "mult": 1.2, "weight": 0.40},
    "510500": {"name": "中证500",  "tier": "satellite","entry": 8,  "td": 0, "exit_full": 50, "exit_half": 50, "fb": 60, "mult": 0.9, "weight": 0.15},
    "159845": {"name": "中证1000", "tier": "satellite","entry": 8,  "td": 1, "exit_full": 70, "exit_half": 70, "fb": 60, "mult": 1.0, "weight": 0.15},
    "159915": {"name": "创业板指", "tier": "satellite","entry": 8,  "td": 1, "exit_full": 80, "exit_half": 80, "fb": 60, "mult": 1.0, "weight": 0.10},
    "510300": {"name": "沪深300",  "tier": "defense", "entry": 5,  "td": 2, "exit_full": 80, "exit_half": 80, "fb": 50, "mult": 0.8, "weight": 0.10},
    "513400": {"name": "道琼斯",   "tier": "core",   "entry": 10, "td": 0, "exit_full": 50, "exit_half": 50, "fb": 40, "mult": 1.2, "weight": 0.25},
    "159632": {"name": "纳斯达克", "tier": "core",   "entry": 8,  "td": 0, "exit_full": 40, "exit_half": 40, "fb": 40, "mult": 1.2, "weight": 0.25},
    "513600": {"name": "恒生指数", "tier": "defense", "entry": 8,  "td": 1, "exit_full": 60, "exit_half": 30, "fb": 20, "mult": 0.8, "weight": 0.10},
}


def fetch_full_series():
    api_key = os.environ.get("ARKVOL_API_KEY", "")
    resp = requests.get(
        "https://arkvol.com/api/funds-greed/alla/series",
        params={"range": "full"},
        headers={"x-api-key": api_key},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("data", resp.json())


def rolling_percentile(values, window=250, min_window=50):
    result = np.full(len(values), np.nan)
    for i in range(1, len(values)):
        start = max(0, i - window + 1)
        wv = values[start : i + 1]
        if len(wv) >= min_window:
            result[i] = (wv < values[i]).sum() / len(wv)
    return result


def simulate_trades(greed, close, entry_pct, td, exit_full, exit_half, fb):
    max_hold = max(60, fb + 10)
    pct = rolling_percentile(greed)
    n = len(greed)
    entries = []
    i = 0
    while i < n - max_hold:
        if np.isnan(pct[i]) or pct[i] >= entry_pct / 100.0:
            i += 1
            continue
        if td > 0:
            if i + td >= n:
                i += 1
                continue
            ok = all(greed[i + j] > greed[i + j - 1] for j in range(1, td + 1))
            if not ok:
                i += 1
                continue
            entry_idx = i + td
        else:
            entry_idx = i
        if np.isnan(close[entry_idx]) or close[entry_idx] <= 0:
            i += 1
            continue

        half_exited = False
        full_exited = False
        half_ret = None
        full_ret = None
        days_to_exit = fb

        for t in range(1, max(fb, 60) + 1):
            idx = entry_idx + t
            if idx >= n:
                break
            if np.isnan(pct[idx]):
                continue
            cur_pct = pct[idx] * 100
            if not half_exited and cur_pct >= exit_half:
                half_ret = (close[idx] - close[entry_idx]) / close[entry_idx]
                half_exited = True
            if not full_exited and cur_pct >= exit_full:
                full_ret = (close[idx] - close[entry_idx]) / close[entry_idx]
                full_exited = True
            if t == fb:
                if not half_exited:
                    half_ret = (close[idx] - close[entry_idx]) / close[entry_idx]
                if not full_exited:
                    full_ret = (close[idx] - close[entry_idx]) / close[entry_idx]
                days_to_exit = t
                break
            if half_exited and full_exited:
                days_to_exit = t
                break

        if half_ret is not None and full_ret is not None:
            combined_ret = 0.5 * half_ret + 0.5 * full_ret
            entries.append({"entry_idx": entry_idx, "ret": combined_ret, "days": days_to_exit})

        skip_to = entry_idx + 5
        while skip_to < n and not np.isnan(pct[skip_to]) and pct[skip_to] < entry_pct / 100.0:
            skip_to += 1
        i = skip_to + 1

    return entries


print("=" * 105)
print("  策略年化收益率测算 (全量数据回测)")
print("=" * 105)

full_data = fetch_full_series()

print(f"\n  {'指数':<10s} {'tier':<10s} {'数据跨度':<20s} {'交易':>4s} {'次/年':>6s} {'平均收益':>8s} {'持天':>5s} {'年化':>8s} {'加权贡献':>8s}")
print(f"  {'─' * 95}")

total_weighted = 0
total_eff_weight = 0

for code, cfg in INDICES.items():
    if code not in full_data:
        continue
    series = full_data[code]
    greed = np.array([float(p["greed"]) for p in series])
    close = np.array([float(p["close"]) for p in series])

    trades = simulate_trades(
        greed, close, cfg["entry"], cfg["td"], cfg["exit_full"], cfg["exit_half"], cfg["fb"]
    )

    if not trades:
        print(f"  {cfg['name']:<10s} {'无交易数据':>50s}")
        continue

    d0 = series[0]["date"]
    d1 = series[-1]["date"]
    days_span = (datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d0, "%Y-%m-%d")).days
    years_span = days_span / 365.25

    n_trades = len(trades)
    trades_per_year = n_trades / years_span

    rets = np.array([t["ret"] for t in trades])
    avg_ret = np.mean(rets)
    avg_days = np.mean([t["days"] for t in trades])

    annual_return = avg_ret * trades_per_year

    weight = cfg["weight"]
    mult = cfg["mult"]
    eff_weight = weight * mult
    contribution = annual_return * eff_weight

    if cfg["tier"] not in ("drop", "watch"):
        total_weighted += contribution
        total_eff_weight += eff_weight

    print(
        f"  {cfg['name']:<10s} {cfg['tier']:<10s} {d0}~{d1}  {n_trades:>3d}次 {trades_per_year:>5.1f}  "
        f"{avg_ret*100:>+7.2f}% {avg_days:>4.0f}天 {annual_return*100:>+7.2f}% {contribution*100:>+7.2f}%"
    )

portfolio_return = total_weighted / total_eff_weight if total_eff_weight > 0 else 0

print(f"  {'─' * 95}")
print(f"  组合期望年化收益率: {portfolio_return*100:+.2f}% (有效权重合计={total_eff_weight:.2f})")
print()

# Detailed stats
print(f"  {'─── 每笔交易明细 ───':^90s}")
print(f"  {'指数':<10s} {'笔数':>4s} {'均值':>8s} {'中位':>8s} {'胜率':>6s} {'最大':>8s} {'最小':>8s} {'std':>8s} {'平均持天':>6s}")
print(f"  {'─' * 75}")
for code, cfg in INDICES.items():
    if code not in full_data:
        continue
    series = full_data[code]
    greed = np.array([float(p["greed"]) for p in series])
    close = np.array([float(p["close"]) for p in series])
    trades = simulate_trades(
        greed, close, cfg["entry"], cfg["td"], cfg["exit_full"], cfg["exit_half"], cfg["fb"]
    )
    if not trades:
        continue
    rets = np.array([t["ret"] for t in trades])
    days_arr = np.array([t["days"] for t in trades])
    print(
        f"  {cfg['name']:<10s} {len(trades):>3d}  "
        f"{np.mean(rets)*100:>+6.2f}% {np.median(rets)*100:>+6.2f}% "
        f"{(rets>0).sum()/len(rets)*100:>5.0f}% "
        f"{np.max(rets)*100:>+6.2f}% {np.min(rets)*100:>+6.2f}% "
        f"{np.std(rets)*100:>6.2f}% {np.mean(days_arr):>5.0f}天"
    )

print()
