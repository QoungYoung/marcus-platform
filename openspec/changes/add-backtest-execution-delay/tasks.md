## 1. Core: simulate_dca() exec_delay parameter

- [x] 1.1 Add `exec_delay: int = 1` parameter to `simulate_dca()` signature
- [x] 1.2 Compute `buy_start = conf_idx + exec_delay` and use it as DCA buy loop start (replace `entry_idx + d` with `buy_start + d`)
- [x] 1.3 Update all exit timing references from `entry_idx` to `buy_start`: holding_days calc, fallback countdown, peak_pct tracking start, staged/combined exit check loop
- [x] 1.4 Update bounds check at signal validation to use `buy_start` (`conf_idx + exec_delay + PIT_WINDOW_DAYS + MAX_HOLD >= n`)
- [x] 1.5 Fix max_concurrent events to use `entry_date_idx`/`exit_date_idx` from trades (already using buy_start-based values)

## 2. Phase runners: pass exec_delay through

- [x] 2.1 Add `exec_delay: int = 1` parameter to `run_phase1()` and forward to all `simulate_dca()` calls
- [x] 2.2 Add `exec_delay: int = 1` parameter to `run_phase2()` and forward to all `simulate_dca()` calls
- [x] 2.3 Add `exec_delay: int = 1` parameter to `walk_forward()` and forward to both train/test `simulate_dca()` calls
- [x] 2.4 Add `exec_delay: int = 1` parameter to `yearly_breakdown()` and forward to `simulate_dca()`
- [x] 2.5 Add `exec_delay: int = 1` parameter to `sensitivity_analysis()` and forward to all `simulate_dca()` calls

## 3. Main runner & metadata

- [x] 3.1 Update `run()` to pass `exec_delay=1` (default) to Phase 1/2/3 calls
- [x] 3.2 Add `"exec_delay": exec_delay` to report metadata (`final["meta"]`)
- [x] 3.3 Add `exec_delay` to printed output header (e.g., "执行延迟: 1天 (T+1)")

## 4. Validation

- [x] 4.1 Run `exec_delay=1` full backtest against all 9 indices, verify results are within expected range (~0.5% lower per-trade than delay=0)
- [x] 4.2 Run `exec_delay=0` and confirm results match previous backtest output (backward compatibility)
- [x] 4.3 Verify report JSON includes `meta.exec_delay` field
