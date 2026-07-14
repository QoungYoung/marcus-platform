"""Backtest: simulate relaxed add-position rules on June 22-26 positions."""
import sys
sys.path.insert(0, 'backend')
from datetime import datetime as dt
from app.core.trading._api_config import get_tushare_pro
pro = get_tushare_pro()

# ── All stocks held during June 22-26 ──
positions = [
    ("SH600460", "600460.SH", "20260622", 43.79, "20260624", 50.23, 100),
    ("SZ002079", "002079.SZ", "20260622", 14.12, "20260626", 13.97, 100),
    ("SZ000938", "000938.SZ", "20260619", 27.58, "20260622", 26.70, 100),
    ("SZ300634", "300634.SZ", "20260619", 22.48, "20260622", 21.24, 200),
    ("SH603993", "603993.SH", "20260622", 21.26, "20260623", 20.43, 200),
    ("SZ000988", "000988.SZ", "20260619", 166.56, "20260623", 164.85, 100),
    ("SH600276", "600276.SH", "20260623", 50.26, "20260625", 49.06, 100),
    ("SZ300433", "300433.SZ", "20260619", 53.53, "20260623", 52.93, 100),
    ("SZ002185", "002185.SZ", "20260624", 20.425, "20260626", 23.41, 200),
    ("SH688396", "688396.SH", "20260624", 85.44, "20260625", 82.95, 100),
    ("SZ000100", "000100.SZ", "20260625", 5.13, None, None, 1000),
    ("SZ002156", "002156.SZ", "20260625", 78.56, "20260626", 76.50, 100),
]

ACCOUNT = 100000  # assumed account size

def calc_indicators(df, idx):
    closes = df['close'].values
    vols = df['vol'].values
    n = len(closes)

    ma5 = float(sum(closes[max(0, idx - 4):idx + 1]) / min(5, idx + 1))
    ma20 = float(sum(closes[max(0, idx - 19):idx + 1]) / min(20, idx + 1)) if idx >= 19 else None

    ma5_slope = None
    if idx >= 10:
        ma5_prev = float(sum(closes[idx - 9:idx - 4]) / 5)
        if ma5_prev > 0:
            ma5_slope = (ma5 - ma5_prev) / ma5_prev

    vol_ratio = None
    if idx >= 5:
        avg_vol = float(sum(vols[max(0, idx - 5):idx]) / min(5, idx))
        vol_ratio = vols[idx] / avg_vol if avg_vol > 0 else 1.0

    return {
        'close': float(closes[idx]),
        'open': float(df['open'].values[idx]),
        'high': float(df['high'].values[idx]),
        'low': float(df['low'].values[idx]),
        'ma5': ma5,
        'ma20': ma20,
        'ma5_slope': ma5_slope,
        'vol_ratio': vol_ratio,
    }


# Fetch daily kline
daily_data = {}
for sym, ts_code, *_ in positions:
    df = pro.daily(ts_code=ts_code, start_date='20260601', end_date='20260630', limit=30)
    if df is not None and not df.empty:
        df = df.sort_values("trade_date", ascending=True)
        daily_data[ts_code] = df

# Simulate
results = []
skipped_no_float = 0
skipped_no_core = 0

for pos in positions:
    sym, ts_code, buy_date_str, buy_price, sell_date_str, sell_price, shares = pos

    if ts_code not in daily_data:
        continue

    df = daily_data[ts_code]
    dates = df['trade_date'].values

    for day_idx in range(len(df)):
        trade_date = dates[day_idx]

        # Skip if before buy or after sell
        if trade_date < buy_date_str:
            continue
        if trade_date == buy_date_str:
            continue  # T+1 locked
        if sell_date_str and trade_date > sell_date_str:
            continue

        ind = calc_indicators(df, day_idx)
        float_pnl_pct = (ind['close'] - buy_price) / buy_price * 100

        if float_pnl_pct < 1.0:
            skipped_no_float += 1
            continue

        # ── New relaxed rules ──
        # Core: MA5 > MA20
        if ind['ma20'] is None or ind['ma5'] <= ind['ma20']:
            skipped_no_core += 1
            continue

        core_detail = f"MA5({ind['ma5']:.2f}) > MA20({ind['ma20']:.2f})"

        # Aux: 5选2 (3 checked, 2 skipped as data-unavailable)
        aux_passed = 0
        aux_total = 0
        aux_detail = []

        if ind['ma5_slope'] is not None:
            aux_total += 1
            if ind['ma5_slope'] > 0:
                aux_passed += 1
                aux_detail.append(f"MA5斜率{ind['ma5_slope']:.2%}>0:YES")
            else:
                aux_detail.append(f"MA5斜率{ind['ma5_slope']:.2%}>0:NO")

        if ind['vol_ratio'] is not None:
            aux_total += 1
            if ind['vol_ratio'] > 0.8:
                aux_passed += 1
                aux_detail.append(f"量比{ind['vol_ratio']:.2f}>0.8:YES")
            else:
                aux_detail.append(f"量比{ind['vol_ratio']:.2f}>0.8:NO")

        vwap_approx = (ind['high'] + ind['low'] + ind['close']) / 3
        aux_total += 1
        if ind['close'] > vwap_approx:
            aux_passed += 1
            aux_detail.append(f"价>VWAP({vwap_approx:.2f}):YES")
        else:
            aux_detail.append(f"价>VWAP({vwap_approx:.2f}):NO")

        # sector flow & moneyflow: skip (data unavailable)
        aux_detail.append("板块资金:SKIP")
        aux_detail.append("主力资金:SKIP")

        if aux_passed < 2:
            continue

        # ── Tier ──
        if float_pnl_pct >= 3.0:
            tier = "sprint"
            tier_cap = 0.25
        elif float_pnl_pct >= 1.0:
            tier = "confirm"
            tier_cap = 0.18
        else:
            tier = "probe"
            tier_cap = 0.10

        # Calculate add size
        current_value = shares * buy_price
        current_pct = current_value / ACCOUNT
        target_value = tier_cap * ACCOUNT
        add_value = max(0, target_value - current_value)
        add_shares = int(add_value / ind['close'] // 100 * 100)

        if add_shares < 100:
            continue  # too small

        # Exit price
        exit_price = None
        exit_reason = ""

        if trade_date == sell_date_str:
            exit_price = sell_price
            exit_reason = "actual_sell_day"
        elif day_idx + 1 < len(df):
            next_date = dates[day_idx + 1]
            next_close = float(df['close'].values[day_idx + 1])
            if sell_date_str and next_date > sell_date_str:
                exit_price = sell_price
                exit_reason = "actual_sell_price"
            else:
                exit_price = next_close
                exit_reason = f"next_day({next_date})"

        if exit_price is None:
            continue

        add_pnl = (exit_price - ind['close']) * add_shares
        orig_pnl = (exit_price - buy_price) * shares if exit_price else 0

        results.append({
            'symbol': sym,
            'date': trade_date,
            'float_pnl': round(float_pnl_pct, 2),
            'tier': tier,
            'close': round(ind['close'], 2),
            'core': core_detail,
            'aux': f"{aux_passed}/{aux_total}",
            'aux_detail': aux_detail,
            'add_shares': add_shares,
            'add_price': round(ind['close'], 2),
            'exit_price': round(exit_price, 2),
            'exit_reason': exit_reason,
            'add_pnl': round(add_pnl, 2),
            'orig_pnl': round(orig_pnl, 2),
        })


# ── Output ──
print(f"Skipped: no_float={skipped_no_float}, no_core={skipped_no_core}")
print(f"Add opportunities found: {len(results)}")
print()

total_add = 0
total_orig = 0

for r in sorted(results, key=lambda x: (x['symbol'], x['date'])):
    flag = "GREEN" if r['add_pnl'] > 0 else "RED  "
    print(f"{flag} {r['symbol']:12s} | {r['date']} | float +{r['float_pnl']:5.2f}% | "
          f"{r['tier']:7s} | close={r['close']:8.2f} | add {r['add_shares']:5d}sh")
    print(f"       Core: {r['core']}")
    print(f"       Aux: {r['aux']} | {' | '.join(r['aux_detail'])}")
    print(f"       Add@{r['add_price']:.2f} -> exit@{r['exit_price']:.2f} ({r['exit_reason']}) | "
          f"add_pnl={r['add_pnl']:+.2f} | orig_pnl={r['orig_pnl']:+.2f}")
    print()
    total_add += r['add_pnl']
    total_orig += r['orig_pnl']

print("=" * 70)
print(f"Total add-position PnL: {total_add:+.2f}")
print(f"Total original PnL:     {total_orig:+.2f}")
print(f"Combined PnL:           {total_add + total_orig:+.2f}")
if results:
    wins = sum(1 for r in results if r['add_pnl'] > 0)
    losses = sum(1 for r in results if r['add_pnl'] < 0)
    even = sum(1 for r in results if r['add_pnl'] == 0)
    print(f"Add win/loss/even: {wins}/{losses}/{even} = {wins/len(results)*100:.0f}% win rate")
    print(f"Avg add PnL: {total_add/len(results):+.2f}")
