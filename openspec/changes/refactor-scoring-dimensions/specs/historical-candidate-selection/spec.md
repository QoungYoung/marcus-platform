## ADDED Requirements

### Requirement: Historical candidate selection by date
The system SHALL select the top-3 stocks by market capitalization within each industry for a specific historical date using data available on or before that date.

#### Scenario: Historical mode uses time-aware selection
- **WHEN** the leaderboard endpoint is called with a `date` parameter
- **THEN** candidate selection SHALL use market cap data from that specific date (not the current stock_pool snapshot)
- **AND** the response SHALL include `survivorship_bias: false` to indicate time-aware data was used

#### Scenario: Real-time mode uses current stock_pool
- **WHEN** the leaderboard endpoint is called without a `date` parameter
- **THEN** candidate selection SHALL use the current stock_pool.db snapshot

### Requirement: Market cap reconstruction from daily data
The system SHALL reconstruct historical market capitalization from Tushare daily table data when a historical date is requested.

#### Scenario: Market cap from daily table
- **WHEN** historical candidate selection is needed for a date
- **THEN** the system SHALL fetch close price and total share data from the daily or daily_basic table for that date
- **AND** market cap SHALL be computed as close × total_share / 10000 (万元)

#### Scenario: Industry classification from stock_pool
- **WHEN** historical candidate selection groups stocks by industry
- **THEN** the industry assignment SHALL use the CURRENT stock_pool industry label (assumed stable over the backtest window)

#### Scenario: Fallback when daily data unavailable
- **WHEN** daily market cap data is unavailable for a historical date
- **THEN** the system SHALL fall back to current stock_pool market cap data
- **AND** the response SHALL include `survivorship_bias: true` to flag the data quality issue

### Requirement: Expanded cross-section in dump_scores
The system SHALL support dumping all eligible candidates per date, not just the top N.

#### Scenario: All candidates mode
- **WHEN** dump_scores.py is called with --limit 0
- **THEN** all candidates passing hard filters SHALL be written to the CSV
- **AND** the daily sample size SHALL be in range [200, 330] rather than fixed at 30

#### Scenario: Default limit unchanged for backward compatibility
- **WHEN** dump_scores.py is called without --limit
- **THEN** the default SHALL remain 30 to match existing behavior
