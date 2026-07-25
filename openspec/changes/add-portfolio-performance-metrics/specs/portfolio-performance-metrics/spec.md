## ADDED Requirements

### Requirement: Performance Metrics Display
The system SHALL display a row of performance metric cards below the portfolio hero section, showing Sharpe ratio, monthly returns, quarterly returns, and benchmark-relative daily performance.

#### Scenario: All metrics available
- **WHEN** the portfolio page loads and equity history has ≥ 20 data points
- **THEN** the system displays Sharpe ratio (formatted to 2 decimal places), the current month's return as a percentage, the current quarter's return as a percentage, and today's performance relative to 沪深300

#### Scenario: Insufficient data for Sharpe
- **WHEN** equity history has fewer than 20 data points
- **THEN** the Sharpe ratio card displays "N/A" with a tooltip explaining more trading days are needed

#### Scenario: Benchmark performance direction
- **WHEN** today's account return exceeds 沪深300 return
- **THEN** the benchmark card shows a green "跑赢 +X.XX%" label
- **WHEN** today's account return is below 沪深300 return
- **THEN** the benchmark card shows a red "跑输 -X.XX%" label

### Requirement: Stock P&L Contribution Ranking
The system SHALL display a horizontal bar chart ranking the top 10 stocks by absolute P&L contribution over the trailing 30-day period.

#### Scenario: Daily breakdown data available
- **WHEN** the 30-day daily P&L breakdown is fetched successfully
- **THEN** the system aggregates `float_pnl + realized_pnl` per stock across all days, sorts by absolute contribution descending, and renders the top 10 as a horizontal bar chart with green bars for positive and red bars for negative contributions

#### Scenario: No breakdown data
- **WHEN** the daily P&L breakdown API returns an empty array (no trades in period)
- **THEN** the system displays a placeholder message "暂无盈亏明细数据" in the contribution section

#### Scenario: Fewer than 10 stocks
- **WHEN** the aggregated breakdown contains fewer than 10 unique stocks
- **THEN** the system renders all available stocks in the ranking without padding empty slots

### Requirement: Benchmark-Relative Daily Performance
The system SHALL compare today's portfolio return against the 沪深300 index return and display the relative outperformance or underperformance.

#### Scenario: Both data sources available
- **WHEN** portfolio summary and market indices are both loaded
- **THEN** the system computes `(totalPnl / (totalAsset - totalPnl)) * 100 - 沪深300.change_pct` and displays the result as a signed percentage

#### Scenario: Market indices not yet loaded
- **WHEN** market indices data is still loading
- **THEN** the benchmark card shows a loading skeleton placeholder

### Requirement: Monthly and Quarterly Return Computation
The system SHALL compute and display calendar-period returns from equity history data.

#### Scenario: Sufficient history for monthly returns
- **WHEN** equity history spans at least 2 calendar months
- **THEN** the system groups equity points by year-month, computes period return for each month as `(last_equity - first_equity) / first_equity`, and displays the last 6 months in a compact labeled list

#### Scenario: Sufficient history for quarterly returns
- **WHEN** equity history spans at least 2 calendar quarters
- **THEN** the system groups equity points by year-quarter, computes period return for each quarter, and displays the last 4 quarters in a compact labeled list

### Requirement: Pure Frontend Computation
All performance metrics SHALL be computed client-side from data already returned by existing API endpoints. No new backend endpoints or modifications to existing endpoints are required.

#### Scenario: No backend changes
- **WHEN** the implementation is deployed
- **THEN** the backend API surface remains unchanged — the same endpoints serve the same response shapes
