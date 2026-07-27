## 1. Per-Index Parameter Configuration

- [x] 1.1 Extend `CHINA_INDICES` dict in `golden_pit_service.py` with per-index fields: `entry_pct` (warning threshold), `pit_pct` (golden pit threshold), `turning_days` (confirmation days), `position_multiplier` (sizing scale), `pre_turn_cap` (cumulative cap ratio)
- [x] 1.2 Update `_build_index_info()` to read thresholds from index config instead of global `PERCENTILE_WARNING`/`PERCENTILE_GOLDEN_PIT`
- [x] 1.3 Update `_detect_trend()` to accept per-index `turning_days` parameter instead of global `TURNING_CONSECUTIVE_DAYS`
- [x] 1.4 Update `golden_pit_dca_service.py` to apply per-index `position_multiplier` and `pre_turn_cap` when calculating daily amounts

## 2. Dynamic Exit Signals

- [x] 2.1 Add `_detect_exit_signal()` method to `GoldenPitService`: calculate exit signal (null/half_exit/full_exit/stop_profit) based on greed percentile and trend state
- [x] 2.2 Include exit_signal and exit_reason fields in each index object within `_build_index_info()` output
- [x] 2.3 Add exit signal display in `format_morning_report()` for indices with active positions

## 3. Sell Order Execution in DCA Service

- [x] 3.1 Add `_place_sell_order()` method to DCA service (symmetric to `_place_buy_order`, sell at limit_price × 0.98)
- [x] 3.2 Add exit signal check at the start of `execute_golden_pit_dca()`: for each held ETF, check if exit signal triggered, execute sell if so
- [x] 3.3 Record sell orders in `GoldenPitDCALog` with strategy field set to exit signal type

## 4. Multi-Index Resonance Coefficient

- [x] 4.1 Add `_resonance_multiplier()` function in `golden_pit_dca_service.py`: count indices in golden_pit status, return multiplier
- [x] 4.2 Apply resonance multiplier to buy order amounts in `execute_golden_pit_dca()`, capped at per-index max_total

## 5. Backtest v7 — Full Entry→Exit Cycle

- [x] 5.1 Create `scripts/backtest_golden_pit_v7.py`: simulate P10 entry → trend tracking → P30/P50 dynamic exit, with per-index parameters
- [x] 5.2 Run backtest with real ArkVol data and output per-index performance table (entry/exit dates, return, max drawdown, win rate)
- [x] 5.3 Calibrate per-index parameters based on backtest results and update `CHINA_INDICES` values

## 6. API & Frontend Updates

- [x] 6.1 Add exit signal fields to `/golden-pit/status` response TypeScript interfaces in `GoldenPitPage.tsx`
- [x] 6.2 Display exit signal badges and resonance status on the golden pit page index cards
- [x] 6.3 Add resonance multiplier info to DCA status endpoint response
