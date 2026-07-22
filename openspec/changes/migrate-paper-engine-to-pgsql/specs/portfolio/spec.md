## MODIFIED Requirements

### Requirement: Account Summary
The system SHALL return current account status by reading `account_info` table from PostgreSQL, computing `position_value` from real-time XueqiuEngine quotes, and deriving `total_asset` as `available_cash + frozen_cash + position_value`.

#### Scenario: Get portfolio summary
- **WHEN** GET /api/v1/portfolio is called
- **THEN** response includes total_assets, available_cash, market_value, total_pl, today_pl, weekly_pl computed from PostgreSQL data

### Requirement: Position List
The system SHALL return all current positions computed via FIFO replay from PostgreSQL `trades` table, with real-time market prices from XueqiuEngine.

#### Scenario: Get positions
- **WHEN** GET /api/v1/portfolio/positions is called
- **THEN** each position includes symbol, name, quantity, avg_cost, current_price, unrealized_pl, unrealized_pl_pct

### Requirement: Equity History
The system SHALL return historical equity curve from PostgreSQL `daily_snapshot` table.

#### Scenario: Get equity history
- **WHEN** GET /api/v1/portfolio/equity is called
- **THEN** response includes list of equity points from `daily_snapshot` with trade_date and total_asset values
