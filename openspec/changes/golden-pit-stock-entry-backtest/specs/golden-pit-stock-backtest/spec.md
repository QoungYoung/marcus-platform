## ADDED Requirements

### Requirement: Golden pit entry date detection from DB snapshots

The system SHALL read historical golden pit entry dates from the `golden_pit_snapshots` PostgreSQL table, identifying rows where `status='golden_pit'` or `greed_value` falls below the per-index `pit_greed` threshold defined in `CHINA_INDICES`.

#### Scenario: Detect golden pit events for backtest

- **WHEN** the backtest script queries `golden_pit_snapshots` for dates between 2020-01-01 and today
- **THEN** the system returns a list of `(entry_date, fund_code, index_name, greed_value)` tuples for all golden pit events
- **AND** consecutive golden pit days for the same index are merged into a single event (earliest date in the cluster)

### Requirement: Index constituent fetching via tushare index_weight

The system SHALL fetch index constituent stocks and their weights from tushare's `index_weight` API for each golden pit entry event, using the month of the entry date as the query month. Results SHALL be cached locally as parquet files to avoid repeated API calls.

#### Scenario: Fetch constituents for CSI 300 golden pit event

- **WHEN** a golden pit event occurs for 510300 (沪深300) on 2026-07-27
- **THEN** the system calls `pro.index_weight(index_code='000300.SH', start_date='20260701', end_date='20260731')`
- **AND** caches the result to `data/backtest/指数数据/index_weight/000300.SH.parquet`
- **AND** returns a list of `(ts_code, weight)` tuples

#### Scenario: Cache hit avoids API call

- **WHEN** the constituents for an index-month combination have been fetched previously
- **THEN** the system reads from the cached parquet file instead of calling tushare API

### Requirement: Offline entry filter evaluation

The system SHALL implement an offline version of the three-layer entry filter (`evaluate_entry_filters_offline`) that accepts pre-computed technical indicators and moneyflow data, and returns the same pass/downgrade/block decision as the real-time `check_entry_filters`, excluding RSR and intraday-specific checks that require proprietary or real-time data.

#### Scenario: Stock passes all three layers

- **WHEN** a stock has MA5 > MA20, MACD golden cross, 5-day main net inflow > 0, RSI6 < 80, KDJ-J < 100, and no KDJ death cross
- **THEN** the filter returns `final_grade='pass'` with `downgrade_multiplier >= 0.8`
- **AND** `hard_block` is `False`

#### Scenario: Stock blocked by 5-day moneyflow

- **WHEN** a stock has 5-day main net inflow < 0
- **THEN** the filter returns `final_grade='blocked'` with `downgrade_multiplier = 0.0`

#### Scenario: Stock downgraded by MACD death cross with no convergence

- **WHEN** a stock has MACD death cross and DIF is not converging
- **THEN** the filter returns `downgrade_multiplier <= 0.5`

### Requirement: Simulated trading at 14:55 on entry date

The system SHALL simulate buying at the 14:55 minute bar price on each golden pit entry date. For each passing stock, it SHALL calculate returns at 15, 20, and 30 trading-day holding periods. The system SHALL compare equal-weight portfolio returns against the corresponding ETF return over the same holding period.

#### Scenario: Calculate stock return vs ETF return

- **WHEN** N stocks pass the entry filter on a golden pit entry date
- **THEN** the system records the buy price (14:55 close), the exit price (N trading days later close), and the ETF return over the same period
- **AND** the stock portfolio return is the equal-weight average of all N stock returns

### Requirement: Results summary and comparison

The system SHALL output a summary table grouped by index (fund_code), showing: number of golden pit events, average number of passing stocks per event, average equal-weight stock portfolio return, average ETF return, excess return (stock - ETF), and win rate. The system SHALL also output per-event detail with date, index, stocks bought, and individual returns.

#### Scenario: Summary output for SSE 50 Index

- **WHEN** the backtest completes for 510050 (上证50)
- **THEN** the output shows total events, avg stocks passing, avg stock return, avg ETF return, and excess return
- **AND** each event has a detail line with date, stock count, and holding-period returns
