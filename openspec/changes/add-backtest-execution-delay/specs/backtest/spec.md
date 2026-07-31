## MODIFIED Requirements

### Requirement: DCA Backtest Simulation Timing
The `simulate_dca()` function in the gold pit ultimate backtest engine SHALL accept an `exec_delay` parameter that shifts DCA buy execution from signal confirmation day to confirmation day + exec_delay, reflecting the real-world constraint that greed data is only available after market close.

#### Scenario: Realistic execution timing
- **WHEN** `simulate_dca()` is called with `exec_delay=1`
- **THEN** DCA buy start SHALL be `entry_idx + 1`
- **THEN** all exit timing (holding days, fallback, trailing stop) SHALL reference buy_start, not entry_idx
- **THEN** signals near data boundaries SHALL be excluded if buy_start + PIT_WINDOW_DAYS + MAX_HOLD exceeds data length

#### Scenario: Idealized execution for comparison
- **WHEN** `simulate_dca()` is called with `exec_delay=0`
- **THEN** behavior SHALL be identical to the pre-delay implementation (buy from entry_idx)
