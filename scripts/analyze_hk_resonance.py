# -*- coding: utf-8 -*-
"""港股份额 × 恒生贪婪 共振确认分析

测试四个维度:
  1. 双极端确认: 份额低 + 贪婪低 → 买入信号是否更强?
  2. 双回暖确认: 份额升 + 贪婪升 → 趋势确认?
  3. 背离场景: 份额升但贪婪降 (or vice versa) → 假信号风险?
  4. 对比单信号 vs 双信号的胜率/收益率
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ── 加载对齐后的数据 ──
csv_path = PROJECT_ROOT / "data" / "hk_correlation_data.csv"
if not csv_path.exists():
    print("请先运行 analyze_hk_share_greed_correlation.py 生成数据")
    sys.exit(1)

import csv

dates = []
data = {}
with open(csv_path, "r", encoding="utf-8") as f:
    reader = csv.reader(f)
    headers = next(reader)
    for h in headers:
        data[h] = []
    for row in reader:
        for i, h in enumerate(headers):
            val = row[i] if i == 0 else (float(row[i]) if row[i] else np.nan)
            data[h].append(val)

dates = data["date"]
hk_share = np.array(data["港股份额(hk_share)"])
greed_130 = np.array(data["513130_greed(恒生科技贪婪)"])
greed_600 = np.array(data["513600_greed(恒生指数贪婪)"])
price_130 = np.array(data["513130_close(恒生科技ETF价格)"])
price_600 = np.array(data["513600_close(恒生指数ETF价格)"])

n = len(dates)
print(f"数据: {n} 天, {dates[0]} ~ {dates[-1]}")

# 用 513130 (恒生科技, 弹性更大) 作为主分析对象
greed = greed_130
price = price_130

# ── 定义极端阈值 ──
# 用滚动分位, 避免前视偏差
LOOKBACK = 120  # 半年滚动窗口
MIN_WINDOW = 30

def rolling_percentile(values, window=LOOKBACK):
    """计算滚动分位值 (无未来信息), 不足窗口时用 expanding window"""
    result = np.full(len(values), np.nan)
    for i in range(1, len(values)):
        start = max(0, i - window + 1)
        window_vals = values[start:i + 1]
        valid = window_vals[~np.isnan(window_vals)]
        if len(valid) >= MIN_WINDOW:
            pct = (valid < values[i]).sum() / len(valid)
            result[i] = pct
    return result

hk_pct = rolling_percentile(hk_share)
greed_pct = rolling_percentile(greed)

# ── 信号定义 ──
# 买入区域: 份额P10以下 / 贪婪P15以下
SHARE_BUY_THRESHOLD = 0.10   # 港股份额滚动分位 < P10
GREED_BUY_THRESHOLD = 0.15   # 贪婪滚动分位 < P15

# 回暖信号: 份额P30以上 / 贪婪P30以上
SHARE_WARM_THRESHOLD = 0.30
GREED_WARM_THRESHOLD = 0.30

# 信号标记
share_extreme = hk_pct < SHARE_BUY_THRESHOLD
greed_extreme = greed_pct < GREED_BUY_THRESHOLD
share_warm = hk_pct > SHARE_WARM_THRESHOLD
greed_warm = greed_pct > GREED_WARM_THRESHOLD

# 共振信号
dual_extreme = share_extreme & greed_extreme    # 双极端 = 强买入
share_only = share_extreme & ~greed_extreme     # 仅份额极端
greed_only = ~share_extreme & greed_extreme     # 仅贪婪极端

# 回暖共振
dual_warm = share_warm & greed_warm             # 双双回暖 = 强退出

print(f"\n有效分位数样本: {np.sum(~np.isnan(hk_pct))}")
print(f"港股份额分位范围: {np.nanmin(hk_pct):.4f} ~ {np.nanmax(hk_pct):.4f}")
print(f"贪婪分位范围: {np.nanmin(greed_pct):.4f} ~ {np.nanmax(greed_pct):.4f}")

# ── 后验分析: 进入极端后 N 日收益 ──
def forward_returns(signal, price_series, horizons=[5, 10, 20, 40]):
    """计算信号触发后各持有期的收益率"""
    results = {h: [] for h in horizons}
    signal_dates = []

    for i in range(len(signal) - max(horizons)):
        if not signal[i]:
            continue
        entry_price = price_series[i]
        if np.isnan(entry_price) or entry_price <= 0:
            continue
        signal_dates.append(dates[i])

        for h in horizons:
            exit_idx = min(i + h, len(price_series) - 1)
            exit_price = price_series[exit_idx]
            if np.isnan(exit_price):
                continue
            ret = (exit_price - entry_price) / entry_price
            results[h].append(ret)

    return results, signal_dates


print("\n" + "=" * 90)
print("  共振确认分析: 不同信号组合的后续收益表现")
print("=" * 90)

# 用贪婪(proxy for 恒生科技指数近月合约) 而非价格本身, 避免价格趋势本身的噪音
# 对价格也做分析
targets = [
    ("513130_greed (恒生科技贪婪)", greed_130),
    ("513600_greed (恒生指数贪婪)", greed_600),
    ("513130_price (恒生科技ETF)", price_130),
    ("513600_price (恒生指数ETF)", price_600),
]

horizons = [5, 10, 20, 40]

for target_name, target_series in targets:
    print(f"\n{'─' * 80}")
    print(f"  目标: {target_name}")
    print(f"{'─' * 80}")

    signals = [
        ("双极端 (份额+P10 & 贪婪+P15)", dual_extreme),
        ("仅份额极端 (份额+P10, 贪婪≥P15)", share_only),
        ("仅贪婪极端 (贪婪+P15, 份额≥P10)", greed_only),
        ("任意极端 (份额或贪婪)", share_extreme | greed_extreme),
    ]

    print(f"\n  {'信号类型':<35s} {'次数':>5s} ", end="")
    for h in horizons:
        print(f"{'%s天均值' % h:>12s} {'%s天中位' % h:>12s} {'%s天胜率' % h:>10s}", end=" ")
    print()

    for sig_name, sig in signals:
        sig_count = np.sum(sig)
        if sig_count < 3:
            print(f"  {sig_name:<35s} {'N/A':>5s}  (信号不足)")
            continue

        results, _ = forward_returns(sig, target_series, horizons)
        print(f"  {sig_name:<35s} {sig_count:>5d} ", end="")
        for h in horizons:
            rets = results[h]
            if len(rets) < 3:
                print(f"{'N/A':>35s}", end=" ")
                continue
            avg_ret = np.mean(rets) * 100
            med_ret = np.median(rets) * 100
            win_rate = np.sum(np.array(rets) > 0) / len(rets) * 100
            print(f"{avg_ret:>+10.2f}% {med_ret:>+10.2f}% {win_rate:>8.1f}%  ", end=" ")
        print()

    # ── 回暖共振 ──
    print(f"\n  --- 回暖/退出信号 ---")
    warm_signals = [
        ("双回暖 (份额+P30 & 贪婪+P30)", dual_warm),
        ("仅份额回暖 (份额>P30, 贪婪≤P30)", share_warm & ~greed_warm),
        ("仅贪婪回暖 (贪婪>P30, 份额≤P30)", greed_warm & ~share_warm),
    ]

    print(f"  {'信号类型':<35s} {'次数':>5s} ", end="")
    for h in horizons:
        print(f"{'%s天均值' % h:>12s} {'%s天中位' % h:>12s} {'%s天胜率' % h:>10s}", end=" ")
    print()

    for sig_name, sig in warm_signals:
        sig_count = np.sum(sig)
        if sig_count < 3:
            print(f"  {sig_name:<35s} {'N/A':>5s}  (信号不足)")
            continue

        results, _ = forward_returns(sig, target_series, horizons)
        print(f"  {sig_name:<35s} {sig_count:>5d} ", end="")
        for h in horizons:
            rets = results[h]
            if len(rets) < 3:
                print(f"{'N/A':>35s}", end=" ")
                continue
            avg_ret = np.mean(rets) * 100
            med_ret = np.median(rets) * 100
            win_rate = np.sum(np.array(rets) > 0) / len(rets) * 100
            print(f"{avg_ret:>+10.2f}% {med_ret:>+10.2f}% {win_rate:>8.1f}%  ", end=" ")
        print()


# ── 背离分析 ──
print(f"\n{'=' * 90}")
print(f"  背离分析: 份额与贪婪方向不一致时")
print(f"{'=' * 90}")

# 计算日变化方向
share_diff = np.diff(hk_share)
greed_diff = np.diff(greed)

share_up = share_diff > 0.01     # 份额上升 (阈值过滤噪音)
share_down = share_diff < -0.01
greed_up = greed_diff > 0.005    # 贪婪上升 (阈值过滤噪音)
greed_down = greed_diff < -0.005

# 背离场景
divergence_share_up_greed_down = share_up & greed_down   # 份额升, 贪婪降 → 资金流入但情绪悲观
divergence_share_down_greed_up = share_down & greed_up   # 份额降, 贪婪升 → 资金流出但情绪乐观
convergence_both_up = share_up & greed_up                 # 共振向上
convergence_both_down = share_down & greed_down           # 共振向下

for target_name, target_series in targets:
    # 计算后续 N 日收益 (相对于 target 的日变化)
    target_diff = np.diff(target_series)
    target_fwd_ret = {}
    for h in horizons:
        target_fwd_ret[h] = np.full(len(target_diff), np.nan)
        for i in range(len(target_diff) - h):
            if not np.isnan(target_series[i + h]) and not np.isnan(target_series[i]):
                target_fwd_ret[h][i] = (target_series[i + h] - target_series[i]) / target_series[i]

    if target_name != targets[0][0]:
        continue  # 只打印第一个目标详情

    print(f"\n  目标: {target_name}")
    scenarios = [
        ("份额↑ + 贪婪↑ (共振向上)", convergence_both_up),
        ("份额↓ + 贪婪↓ (共振向下)", convergence_both_down),
        ("份额↑ + 贪婪↓ (背离: 资金进情绪差)", divergence_share_up_greed_down),
        ("份额↓ + 贪婪↑ (背离: 资金出情绪好)", divergence_share_down_greed_up),
    ]

    print(f"  {'场景':<38s} {'次数':>5s}", end="")
    for h in horizons:
        print(f"  {'%sd后均值' % h:>12s}", end="")
    print()

    for scen_name, scen in scenarios:
        idx = np.where(scen)[0]
        count = len(idx)
        if count < 3:
            continue
        print(f"  {scen_name:<38s} {count:>5d}", end="")
        for h in horizons:
            rets = target_fwd_ret[h][idx]
            rets = rets[~np.isnan(rets)]
            if len(rets) < 3:
                print(f"  {'N/A':>12s}", end="")
            else:
                avg = np.mean(rets) * 100
                print(f"  {avg:>+10.2f}%", end="")
        print()


# ── 综合评分: 共振强度指数 ──
print(f"\n{'=' * 90}")
print(f"  共振强度指数: 综合评分买入/卖出信号质量")
print(f"{'=' * 90}")

# 信号打分
# 份额分位越低越好 (买入) / 越高越好 (卖出)
# 贪婪分位越低越好 (买入) / 越高越好 (卖出)
# 共振得分 = 份额得分 + 贪婪得分

share_buy_score = np.clip(1 - hk_pct / SHARE_BUY_THRESHOLD, 0, 2)  # 份额越低分越高
greed_buy_score = np.clip(1 - greed_pct / GREED_BUY_THRESHOLD, 0, 2)
resonance_buy = share_buy_score + greed_buy_score  # 0~4 分

# 分组分析
score_bins = [
    ("0-1分 (弱信号)", (resonance_buy >= 0) & (resonance_buy < 1)),
    ("1-2分 (中等)", (resonance_buy >= 1) & (resonance_buy < 2)),
    ("2-3分 (强信号)", (resonance_buy >= 2) & (resonance_buy < 3)),
    ("3-4分 (极强共振)", (resonance_buy >= 3)),
]

print("\n  买入共振得分 vs 后续收益 (target: 513130_greed):")
print(f"  {'得分区间':<25s} {'次数':>6s}", end="")
for h in horizons:
    print(f"  {'%s天均值' % h:>10s} {'胜率':>8s}", end="")
print()

for bin_name, bin_mask in score_bins:
    idx = np.where(bin_mask & ~np.isnan(greed))[0]
    count = len(idx)
    if count < 5:
        continue
    print(f"  {bin_name:<25s} {count:>6d}", end="")
    for h in horizons:
        rets = []
        for i in idx:
            exit_i = min(i + h, n - 1)
            if not np.isnan(greed[i]) and not np.isnan(greed[exit_i]) and greed[i] > 0:
                rets.append((greed[exit_i] - greed[i]) / greed[i])
        if len(rets) < 5:
            print(f"  {'N/A':>18s}", end="")
        else:
            avg_r = np.mean(rets) * 100
            wr = np.sum(np.array(rets) > 0) / len(rets) * 100
            print(f"  {avg_r:>+8.2f}% {wr:>6.1f}%", end="")
    print()


print("\n分析完成。")
