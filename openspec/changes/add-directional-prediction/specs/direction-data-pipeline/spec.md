## ADDED Requirements

### Requirement: Historical data collection script
The system SHALL provide `scripts/dump_direction_data.py` that iterates historical trading days, fetches moneyflow/daily/index data via Tushare, computes direction features, merges forward returns, and outputs a CSV with features and binary labels.

#### Scenario: Full collection run
- **WHEN** the script is invoked with `--days 60 --output data/direction_data.csv`
- **THEN** it produces a CSV with one row per stock per date, containing all direction features and binary target columns (target_1d, target_3d, target_5d)

#### Scenario: Incremental collection
- **WHEN** the script is invoked with `--start-date 20260701`
- **THEN** it only collects data from that date forward, appending to existing output

### Requirement: Binary label construction
The system SHALL construct binary labels from forward returns: target_1d = (next_day_pct > 0).astype(int), target_3d = (day3_pct > 0).astype(int), target_5d = (day5_pct > 0).astype(int). Forward returns SHALL be computed from Tushare `daily` data without lookahead bias.

#### Scenario: Label assignment
- **WHEN** a stock has valid forward return data for all three horizons
- **THEN** all three binary targets are set; missing horizons leave the corresponding target as NaN

### Requirement: API call optimization
The data pipeline SHALL batch API calls by date where possible (moneyflow supports date-based queries for all stocks at once), minimizing Tushare API calls. Total expected API calls per trading day SHALL not exceed 5.

#### Scenario: Moneyflow batch fetch
- **WHEN** moneyflow data is needed for a trading date
- **THEN** the script fetches all stocks for that date in a single Tushare `moneyflow` call, rather than per-stock queries
