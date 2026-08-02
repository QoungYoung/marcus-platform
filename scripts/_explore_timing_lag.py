# -*- coding: utf-8 -*-
"""Explore: quantify T+1 execution delay impact on golden pit DCA strategy.

Compares delay=0 (idealized, buy from signal day) vs delay=1 (realistic, buy from NEXT trading day).
This is a one-off analysis script, not production code.
"""
import json, os, sys, time, numpy as np
from urllib.request import Request, urlopen

import dotenv; from pathlib import Path
dotenv.load_dotenv(Path(__file__).resolve().parent.parent / ".env")
api_key = os.environ.get('ARKVOL_API_KEY', '')

# Fetch full series
print('Fetching full series from API...')
req = Request('https://arkvol.com/api/funds-greed/alla/series?range=full', headers={
    'X-API-Key': api_key, 'Accept': 'application/json',
})
with urlopen(req, timeout=60) as resp:
    data = json.loads(resp.read().decode('utf-8'))
raw = data.get('data', data)

INDICES = {
    '588000': {'name': '科创50', 'min_history': 120},
    '510500': {'name': '中证500', 'min_history': 120},
    '159845': {'name': '中证1000', 'min_history': 120},
    '159915': {'name': '创业板指', 'min_history': 120},
    '510300': {'name': '沪深300', 'min_history': 120},
    '510050': {'name': '上证50', 'min_history': 120},
    '513400': {'name': '道琼斯指数', 'min_history': 60},
    '159632': {'name': '纳斯达克', 'min_history': 60},
    '513600': {'name': '恒生指数', 'min_history': 120},
}

ROLLING_WINDOW = 500
MIN_WINDOW = 50
PIT_WINDOW_DAYS = 15
SIGNAL_COOLDOWN = 5
MIN_TRADES = 8
MAX_HOLD = 60


def simulate_dca_with_delay(greeds, closes, dates, entry_cfg, exit_cfg, min_history, exec_delay=0):
    """
    exec_delay: number of trading days to delay execution after signal.
    0 = idealized (buy from signal day)
    1 = realistic (buy from NEXT trading day, T+1 data availability)
    """
    n = len(greeds)

    # DCA weights
    n_w = PIT_WINDOW_DAYS
    strategy = entry_cfg['dca_strategy']
    strats = {
        'uniform_3': np.array([1.0]*3 + [0.0]*12),
        'uniform_5': np.array([1.0]*5 + [0.0]*10),
        'uniform_7': np.array([1.0]*7 + [0.0]*8),
        'uniform_10': np.array([1.0]*10 + [0.0]*5),
        'uniform_15': np.ones(n_w),
        'front_loaded': np.array([n_w - i for i in range(n_w)], dtype=float),
        'back_loaded': np.array([i + 1 for i in range(n_w)], dtype=float),
        'triangle': np.array([(i+1) if i < 7 else (n_w-i) for i in range(n_w)], dtype=float),
        'lump_entry': np.array([1.0] + [0.0]*14),
    }
    raw_w = strats.get(strategy, strats['uniform_10'])
    dca_weights = raw_w / raw_w.sum()

    # Compute rolling percentile for exit logic
    pct = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        start = max(0, i - ROLLING_WINDOW + 1)
        wv = greeds[start:i + 1]
        if len(wv) >= MIN_WINDOW:
            pct[i] = (wv < greeds[i]).sum() / len(wv) * 100.0

    # Find signals
    signals = []
    last_signal_idx = -999
    skip_until = -1

    for i in range(min_history, n - MAX_HOLD - PIT_WINDOW_DAYS):
        if i < skip_until:
            continue
        if i - last_signal_idx < SIGNAL_COOLDOWN:
            continue
        if greeds[i] > entry_cfg['threshold_value']:
            continue

        turning_days = entry_cfg['turning_days']
        if turning_days > 0:
            if i + turning_days >= n:
                continue
            confirmed = True
            for j in range(1, turning_days + 1):
                if greeds[i + j] <= greeds[i + j - 1]:
                    confirmed = False
                    break
            if not confirmed:
                continue
            entry_idx = i + turning_days
        else:
            entry_idx = i

        if entry_idx + PIT_WINDOW_DAYS + MAX_HOLD >= n:
            continue
        if np.isnan(closes[entry_idx]) or closes[entry_idx] <= 0:
            continue

        last_signal_idx = i
        signals.append((i, entry_idx))
        skip_until = entry_idx + SIGNAL_COOLDOWN

    if len(signals) == 0:
        return {'trades': 0, 'cagr': 0.0, 'win_rate': 0.0, 'avg_return': 0.0,
                'calendar_span': 0, 'signals': 0}

    # Execute trades
    trades = []
    for sig_idx, entry_idx in signals:
        buy_start = entry_idx + exec_delay
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

        # Simple staged exit using greed percentile
        half_exited = False
        full_exited = False
        total_ret = 0.0
        exit_date_idx = buy_start + PIT_WINDOW_DAYS
        exit_type_used = 'fallback'
        half_ret = 0.0

        half_pct = exit_cfg.get('half_exit_pct', 30)
        full_pct = exit_cfg.get('full_exit_pct', 50)
        fallback_days = exit_cfg.get('fallback_days', 40)

        start_check = buy_start + 1
        max_check = min(buy_start + MAX_HOLD + 1, n)

        for j in range(start_check, max_check):
            if full_exited:
                break

            holding_days = j - buy_start
            cur_pct = pct[j]

            if np.isnan(cur_pct):
                continue

            if not half_exited and cur_pct >= half_pct:
                half_ret = (closes[j] - avg_entry) / avg_entry
                half_exited = True
                exit_type_used = 'half_exit'

            if cur_pct >= full_pct and not full_exited:
                if half_exited:
                    total_ret = 0.5 * half_ret + 0.5 * (closes[j] - avg_entry) / avg_entry
                else:
                    total_ret = (closes[j] - avg_entry) / avg_entry
                exit_date_idx = j
                exit_type_used = 'full_exit'
                full_exited = True
                break

            if holding_days >= fallback_days and not full_exited:
                if half_exited:
                    total_ret = 0.5 * half_ret + 0.5 * (closes[j] - avg_entry) / avg_entry
                else:
                    total_ret = (closes[j] - avg_entry) / avg_entry
                exit_date_idx = j
                exit_type_used = 'fallback'
                full_exited = True
                break

        if not full_exited:
            exit_date_idx = min(buy_start + MAX_HOLD, n - 1)
            total_ret = (closes[exit_date_idx] - avg_entry) / avg_entry
            exit_type_used = 'fallback_end'

        trades.append({
            'entry_idx': buy_start,
            'exit_idx': exit_date_idx,
            'return': float(total_ret),
            'exit_signal': exit_type_used,
        })

    if len(trades) < 3:
        return {'trades': len(trades), 'cagr': 0.0, 'win_rate': 0.0, 'avg_return': 0.0,
                'calendar_span': 0, 'signals': len(signals)}

    returns = np.array([t['return'] for t in trades])
    win_rate = float(np.sum(returns > 0) / len(returns))

    first_entry = trades[0]['entry_idx']
    last_exit = trades[-1]['exit_idx']
    calendar_span = max(1, last_exit - first_entry)

    total_factor = float(np.prod(1.0 + returns))
    cagr = float(total_factor ** (252.0 / calendar_span) - 1.0) if total_factor > 0 else 0.0

    avg_ret = float(np.mean(returns))

    return {
        'trades': len(trades),
        'signals': len(signals),
        'cagr': round(cagr, 4),
        'win_rate': round(win_rate, 4),
        'avg_return': round(avg_ret, 4),
        'calendar_span': int(calendar_span),
    }


# Build config grid
entry_configs = []
for thresh in [0.30, 0.32, 0.35, 0.38, 0.40]:
    for turning in [0, 1, 2]:
        for strategy in ['uniform_3', 'uniform_5', 'uniform_10']:
            entry_configs.append({
                'threshold_type': 'fixed',
                'threshold_value': thresh,
                'turning_days': turning,
                'dca_strategy': strategy,
                'position_weighting': 'none',
            })

exit_configs = [
    {'type': 'staged', 'half_exit_pct': 30, 'full_exit_pct': 50, 'fallback_days': 40},
    {'type': 'staged', 'half_exit_pct': 35, 'full_exit_pct': 60, 'fallback_days': 30},
]

print()
print('=' * 110)
print('  T+1 执行延迟 vs 同日执行 — 全量数据对比')
print('  对比: delay=0 (理想化/回测同日买入) vs delay=1 (现实/T+1买入)')
print('=' * 110)

# Process each index
summary_rows = []
for code, cfg in INDICES.items():
    series_raw = raw.get(code, [])
    if not series_raw:
        continue
    series = sorted(series_raw, key=lambda x: x.get('date', ''))
    g = np.array([float(s.get('greed', 0)) for s in series])
    c = np.array([float(s.get('close', 0)) for s in series])
    dts = [s.get('date', '') for s in series]

    if len(g) < cfg['min_history'] + 30:
        continue

    name = cfg['name']
    n_days = len(g)

    best_d0 = {'cagr': -999, 'trades': 0}
    best_d1 = {'cagr': -999, 'trades': 0}

    for entry in entry_configs:
        for exit_cfg in exit_configs:
            r0 = simulate_dca_with_delay(g, c, dts, entry, exit_cfg, cfg['min_history'], exec_delay=0)
            r1 = simulate_dca_with_delay(g, c, dts, entry, exit_cfg, cfg['min_history'], exec_delay=1)
            if r0['trades'] >= MIN_TRADES and r0['cagr'] > best_d0['cagr']:
                best_d0 = r0
            if r1['trades'] >= MIN_TRADES and r1['cagr'] > best_d1['cagr']:
                best_d1 = r1

    cagr_d0 = best_d0.get('cagr', 0)
    cagr_d1 = best_d1.get('cagr', 0)
    wr_d0 = best_d0.get('win_rate', 0)
    wr_d1 = best_d1.get('win_rate', 0)
    tr_d0 = best_d0.get('trades', 0)
    tr_d1 = best_d1.get('trades', 0)

    delta_cagr = cagr_d1 - cagr_d0
    delta_wr = wr_d1 - wr_d0

    print(f'  {name:<8s} ({code})  {n_days}天')
    print(f'    delay=0 (同日): CAGR={cagr_d0:+.2%}  Win={wr_d0:.0%}  Trades={tr_d0}  '
          f'Ret={best_d0.get("avg_return",0):+.2%}')
    print(f'    delay=1 (T+1):  CAGR={cagr_d1:+.2%}  Win={wr_d1:.0%}  Trades={tr_d1}  '
          f'Ret={best_d1.get("avg_return",0):+.2%}')
    print(f'    Δ:              CAGR={delta_cagr:+.2%}  Win={delta_wr:+.0%}  '
          f'Tr={tr_d1 - tr_d0:+d}')

    summary_rows.append({
        'name': name, 'code': code, 'n_days': n_days,
        'cagr_d0': cagr_d0, 'cagr_d1': cagr_d1,
        'wr_d0': wr_d0, 'wr_d1': wr_d1,
        'tr_d0': tr_d0, 'tr_d1': tr_d1,
        'ret_d0': best_d0.get('avg_return', 0),
        'ret_d1': best_d1.get('avg_return', 0),
        'delta_cagr': delta_cagr, 'delta_wr': delta_wr,
    })

print()
print('=' * 110)
print('  汇总对比')
print('=' * 110)
print(f'  {"指数":<10s} {"同日CAGR":>8s} {"T+1CAGR":>8s} {"ΔCAGR":>8s} {"同日Win":>7s} {"T+1Win":>7s} '
      f'{"同日Tr":>6s} {"T+1Tr":>6s} {"同日Ret":>8s} {"T+1Ret":>8s}')
print(f'  {"-"*90}')

for row in summary_rows:
    print(f'  {row["name"]:<10s} {row["cagr_d0"]:>+7.2%} {row["cagr_d1"]:>+7.2%} {row["delta_cagr"]:>+7.2%} '
          f'{row["wr_d0"]:>6.0%} {row["wr_d1"]:>6.0%} '
          f'{row["tr_d0"]:>6d} {row["tr_d1"]:>6d} '
          f'{row["ret_d0"]:>+7.2%} {row["ret_d1"]:>+7.2%}')

if summary_rows:
    avg_delta = np.mean([r['delta_cagr'] for r in summary_rows])
    avg_d0 = np.mean([r['cagr_d0'] for r in summary_rows])
    avg_d1 = np.mean([r['cagr_d1'] for r in summary_rows])
    avg_ret_d0 = np.mean([r['ret_d0'] for r in summary_rows])
    avg_ret_d1 = np.mean([r['ret_d1'] for r in summary_rows])
    print(f'  {"-"*90}')
    print(f'  {"等权平均":<10s} {avg_d0:>+7.2%} {avg_d1:>+7.2%} {avg_delta:>+7.2%} '
          f'{"":>7s} {"":>7s} {"":>6s} {"":>6s} '
          f'{avg_ret_d0:>+7.2%} {avg_ret_d1:>+7.2%}')

    worse = sum(1 for r in summary_rows if r['delta_cagr'] < -0.005)
    better = sum(1 for r in summary_rows if r['delta_cagr'] > 0.005)
    same = len(summary_rows) - worse - better
    print(f'\n  T+1 延迟后: {worse}个指数变差, {better}个变好, {same}个基本持平')

# ---- Detailed per-trade analysis for a key index ----
print()
print('=' * 110)
print('  单指数详细分析: 科创50 (588000) — 逐笔交易对比')
print('=' * 110)

code = '588000'
series_raw = raw.get(code, [])
series = sorted(series_raw, key=lambda x: x.get('date', ''))
g = np.array([float(s.get('greed', 0)) for s in series])
c = np.array([float(s.get('close', 0)) for s in series])
dts = [s.get('date', '') for s in series]
min_hist = INDICES[code]['min_history']

# Use a specific config for detailed comparison
entry_cfg = {'threshold_type': 'fixed', 'threshold_value': 0.35, 'turning_days': 1,
             'dca_strategy': 'uniform_10', 'position_weighting': 'none'}
exit_cfg = {'type': 'staged', 'half_exit_pct': 30, 'full_exit_pct': 50, 'fallback_days': 40}

# Run both with full trade details
n = len(g)
# Reuse the function but collect more details...

# Manual detailed simulation
def detailed_sim(greeds, closes, dates, entry_cfg, exit_cfg, min_history, exec_delay):
    n = len(greeds)
    n_w = PIT_WINDOW_DAYS
    strategy = entry_cfg['dca_strategy']
    strats = {
        'uniform_3': np.array([1.0]*3 + [0.0]*12),
        'uniform_5': np.array([1.0]*5 + [0.0]*10),
        'uniform_7': np.array([1.0]*7 + [0.0]*8),
        'uniform_10': np.array([1.0]*10 + [0.0]*5),
        'uniform_15': np.ones(n_w),
        'front_loaded': np.array([n_w - i for i in range(n_w)], dtype=float),
        'back_loaded': np.array([i + 1 for i in range(n_w)], dtype=float),
        'triangle': np.array([(i+1) if i < 7 else (n_w-i) for i in range(n_w)], dtype=float),
        'lump_entry': np.array([1.0] + [0.0]*14),
    }
    raw_w = strats.get(strategy, strats['uniform_10'])
    dca_weights = raw_w / raw_w.sum()

    pct = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        start = max(0, i - ROLLING_WINDOW + 1)
        wv = greeds[start:i + 1]
        if len(wv) >= MIN_WINDOW:
            pct[i] = (wv < greeds[i]).sum() / len(wv) * 100.0

    signals = []
    last_signal_idx = -999
    skip_until = -1
    for i in range(min_history, n - MAX_HOLD - PIT_WINDOW_DAYS):
        if i < skip_until:
            continue
        if i - last_signal_idx < SIGNAL_COOLDOWN:
            continue
        if greeds[i] > entry_cfg['threshold_value']:
            continue
        turning_days = entry_cfg['turning_days']
        if turning_days > 0:
            if i + turning_days >= n:
                continue
            confirmed = all(greeds[i + j] > greeds[i + j - 1] for j in range(1, turning_days + 1))
            if not confirmed:
                continue
            entry_idx = i + turning_days
        else:
            entry_idx = i
        if entry_idx + PIT_WINDOW_DAYS + MAX_HOLD >= n:
            continue
        if np.isnan(closes[entry_idx]) or closes[entry_idx] <= 0:
            continue
        last_signal_idx = i
        signals.append((i, entry_idx))
        skip_until = entry_idx + SIGNAL_COOLDOWN

    trades = []
    for sig_idx, entry_idx in signals:
        buy_start = entry_idx + exec_delay
        buy_prices = []
        buy_weights_list = []
        first_buy_day = None
        for d in range(PIT_WINDOW_DAYS):
            day = buy_start + d
            if day < n and dca_weights[d] > 0 and closes[day] > 0 and not np.isnan(closes[day]):
                buy_prices.append(closes[day])
                buy_weights_list.append(dca_weights[d])
                if first_buy_day is None:
                    first_buy_day = day

        if len(buy_prices) == 0:
            continue

        buy_prices_arr = np.array(buy_prices)
        buy_weights_arr = np.array(buy_weights_list)
        buy_weights_arr = buy_weights_arr / buy_weights_arr.sum()
        avg_entry = float(np.average(buy_prices_arr, weights=buy_weights_arr))

        half_exited = False
        full_exited = False
        total_ret = 0.0
        exit_date_idx = buy_start + PIT_WINDOW_DAYS
        half_ret = 0.0

        for j in range(buy_start + 1, min(buy_start + MAX_HOLD + 1, n)):
            if full_exited:
                break
            cur_pct = pct[j]
            if np.isnan(cur_pct):
                continue
            if not half_exited and cur_pct >= exit_cfg['half_exit_pct']:
                half_ret = (closes[j] - avg_entry) / avg_entry
                half_exited = True
            if cur_pct >= exit_cfg['full_exit_pct'] and not full_exited:
                total_ret = 0.5 * half_ret + 0.5 * (closes[j] - avg_entry) / avg_entry if half_exited else (closes[j] - avg_entry) / avg_entry
                exit_date_idx = j
                full_exited = True
                break
            if j - buy_start >= exit_cfg['fallback_days'] and not full_exited:
                total_ret = 0.5 * half_ret + 0.5 * (closes[j] - avg_entry) / avg_entry if half_exited else (closes[j] - avg_entry) / avg_entry
                exit_date_idx = j
                full_exited = True
                break
        if not full_exited:
            exit_date_idx = min(buy_start + MAX_HOLD, n - 1)
            total_ret = (closes[exit_date_idx] - avg_entry) / avg_entry

        trades.append({
            'signal_date': dates[entry_idx],
            'first_buy_date': dates[first_buy_day] if first_buy_day else '?',
            'exit_date': dates[exit_date_idx] if exit_date_idx < len(dates) else '?',
            'avg_entry': round(avg_entry, 4),
            'return': round(float(total_ret), 4),
            'n_buys': len(buy_prices),
        })

    return trades

trades_d0 = detailed_sim(g, c, dts, entry_cfg, exit_cfg, INDICES[code]['min_history'], exec_delay=0)
trades_d1 = detailed_sim(g, c, dts, entry_cfg, exit_cfg, INDICES[code]['min_history'], exec_delay=1)

# Align and compare
print(f'  配置: greed<{entry_cfg["threshold_value"]}, turning={entry_cfg["turning_days"]}d, '
      f'{entry_cfg["dca_strategy"]}, exit={exit_cfg["half_exit_pct"]}/{exit_cfg["full_exit_pct"]}%')
print()
print(f'  {"信号日期":<12s} {"同日买入日":<12s} {"T+1买入日":<12s} {"同日收益":>10s} {"T+1收益":>10s} {"差异":>10s}')
print(f'  {"-"*70}')

min_trades = min(len(trades_d0), len(trades_d1))
aligned_returns = []
for i in range(min_trades):
    t0, t1 = trades_d0[i], trades_d1[i]
    delta = t1['return'] - t0['return']
    aligned_returns.append({'d0': t0['return'], 'd1': t1['return'], 'delta': delta})
    print(f'  {t0["signal_date"]:<12s} {t0["first_buy_date"]:<12s} {t1["first_buy_date"]:<12s} '
          f'{t0["return"]:>+9.2%} {t1["return"]:>+9.2%} {delta:>+9.2%}')

if aligned_returns:
    d0_rets = [r['d0'] for r in aligned_returns]
    d1_rets = [r['d1'] for r in aligned_returns]
    deltas = [r['delta'] for r in aligned_returns]
    print(f'  {"-"*70}')
    print(f'  {"汇总":<12s} {"":<12s} {"":<12s} '
          f'{np.mean(d0_rets):>+9.2%} {np.mean(d1_rets):>+9.2%} {np.mean(deltas):>+9.2%}')
    print(f'  T+1延迟后: {sum(1 for d in deltas if d < -0.005)}笔变差 / {sum(1 for d in deltas if d > 0.005)}笔变好 '
          f'/ {sum(1 for d in deltas if abs(d) <= 0.005)}笔持平 (共{len(deltas)}笔)')

print()
print('=' * 110)
print('  结论分析')
print('=' * 110)
print()
print('  问题: 回测假设同日买入(exec_delay=0)，现实是T+1数据 → T日交易')
print('  T+1延迟意味着买入价格比回测假设晚1个交易日')
print('  在上涨市场中: 晚1天买入 = 更高的买入成本 = 更低收益')
print('  在下跌市场中: 晚1天买入 = 更低的买入成本 = 更高收益 (拖后腿变有利)')
print()
