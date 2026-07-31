## Why

Gold pit DCA backtesting assumes same-day execution (signal detected on day D, buying starts day D), but production uses ArkVol API data which is only available after market close. This creates a 1-day look-ahead bias: the backtest "knows" day D's greed value and trades on day D, while the real system reads day D's data on day D+1 morning. Controlled testing across all 9 indices shows this bias inflates per-trade returns by ~0.46% on average for A-shares, compounding to meaningful CAGR overestimation (e.g., 沪深300: 11.0% → 9.8%).

## What Changes

- Add `exec_delay` parameter to `simulate_dca()` in `backtest_golden_pit_ultimate.py`, defaulting to `1` (realistic T+1 execution)
- Shift DCA buy start from `entry_idx` to `entry_idx + exec_delay` — signal detection logic unchanged
- Shift all exit timing checks (staged, full_only, trailing_stop, time_decay, combined) to reference `buy_start` instead of `entry_idx`
- Add intraday gap filter: if next-day open price is >2% above signal-day close, skip first DCA installment for US/HK indices
- Update Phase 1/2/3 runners to pass `exec_delay` through
- **BREAKING**: Default `exec_delay=1` will produce different (lower, more realistic) CAGR numbers than previous backtest runs. Old `exec_delay=0` behavior available via parameter override.

## Capabilities

### New Capabilities

- `backtest-execution-delay`: Realistic execution timing in gold pit DCA backtesting, with configurable delay between signal detection and first buy execution

### Modified Capabilities

- `backtest`: `simulate_dca()` semantics change — buy start shifts from signal confirmation day to confirmation day + exec_delay; this is a spec-level behavioral change
- `golden-pit-dca-schedule`: DCA entry timing now accounts for data availability lag; the schedule reference point moves from signal-day to next-trading-day

## Impact

- **Backtest engine**: `scripts/backtest_golden_pit_ultimate.py` — `simulate_dca()` and all runner functions
- **Future backtest scripts**: Any script copying the `simulate_dca` pattern should adopt `exec_delay=1`
- **DCA service**: `backend/app/services/golden_pit_dca_service.py` — already operates on T+1 data in production (reads T-1 greed, buys T); no changes needed but backtest now matches its real-world timing
- **Backtest reports**: Previous backtest JSON reports (e.g., `golden_pit_ultimate_report.json`) will show optimistic CAGR vs. new `exec_delay=1` runs
