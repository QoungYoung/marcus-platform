## ADDED Requirements

### Requirement: Configurable execution delay
The `simulate_dca()` function SHALL accept an `exec_delay` parameter (int, default 1) that shifts the DCA buy start by N trading days after signal confirmation.

#### Scenario: Default realistic timing
- **WHEN** `simulate_dca()` is called without explicit `exec_delay`
- **THEN** DCA buy start SHALL be `entry_idx + 1` (next trading day after signal confirmation)
- **THEN** signal detection logic SHALL NOT be affected by exec_delay

#### Scenario: Idealized timing for comparison
- **WHEN** `simulate_dca()` is called with `exec_delay=0`
- **THEN** DCA buy start SHALL be `entry_idx` (same day as signal confirmation)
- **THEN** behavior SHALL match the pre-delay backtest semantics exactly

#### Scenario: Custom delay
- **WHEN** `simulate_dca()` is called with `exec_delay=2`
- **THEN** DCA buy start SHALL be `entry_idx + 2`

### Requirement: Exit timing references buy start
All exit logic SHALL use `buy_start` (= `entry_idx + exec_delay`) as the reference point for holding period calculations.

#### Scenario: Holding days calculation
- **WHEN** a DCA trade's buy_start is `entry_idx + 1`
- **THEN** `holding_days` SHALL equal `exit_date_idx - buy_start`
- **THEN** fallback day countdown SHALL start from `buy_start`, not `entry_idx`

#### Scenario: Trailing stop peak tracking
- **WHEN** trailing stop exit type is used
- **THEN** peak greed percentile tracking SHALL start from `buy_start`
- **THEN** peak calculation SHALL use `pct[buy_start:current_idx + 1]`

### Requirement: Bounds checking accounts for delay
The signal validation SHALL exclude signals where `entry_idx + exec_delay + PIT_WINDOW_DAYS + MAX_HOLD >= n`.

#### Scenario: Signal near data boundary
- **WHEN** a signal's `entry_idx + exec_delay + PIT_WINDOW_DAYS + MAX_HOLD >= len(data)`
- **THEN** the signal SHALL be skipped (cannot complete DCA + exit within available data)

### Requirement: Phase runners pass exec_delay
Phase 1, Phase 2, Phase 3 (walk-forward), yearly breakdown, and sensitivity analysis runners SHALL accept and forward `exec_delay` to `simulate_dca()`.

#### Scenario: Phase 1 entry optimization with delay
- **WHEN** `run_phase1()` is called with `exec_delay=1`
- **THEN** all `simulate_dca()` calls within Phase 1 SHALL use `exec_delay=1`

#### Scenario: Phase 2 exit optimization with delay
- **WHEN** `run_phase2()` is called with `exec_delay=1`
- **THEN** all `simulate_dca()` calls within Phase 2 SHALL use `exec_delay=1`

### Requirement: Report metadata includes exec_delay
The final output report SHALL include the `exec_delay` value in its metadata section.

#### Scenario: Report generation
- **WHEN** the backtest completes and generates a JSON report
- **THEN** `meta.exec_delay` SHALL be present and set to the value used during the run
