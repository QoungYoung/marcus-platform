## Purpose

Portfolio management API — account summary, positions, equity history, and weekly P&L tracking for the A-share trading platform.

## Requirements

### Requirement: Account Summary
The system SHALL return current account status including total assets, available cash, market value, and total P&L.

#### Scenario: Get portfolio summary
- **WHEN** GET /api/v1/portfolio is called
- **THEN** response includes total_assets, available_cash, market_value, total_pl, today_pl, weekly_pl

### Requirement: Position List
The system SHALL return all current positions with cost basis, current price, and unrealized P&L.

#### Scenario: Get positions
- **WHEN** GET /api/v1/portfolio/positions is called
- **THEN** each position includes symbol, name, quantity, avg_cost, current_price, unrealized_pl, unrealized_pl_pct

### Requirement: Equity History
The system SHALL return historical equity curve data points.

#### Scenario: Get equity history
- **WHEN** GET /api/v1/portfolio/equity is called
- **THEN** response includes list of equity points with timestamp and total_equity values

### Requirement: Weekly P&L
The system SHALL calculate and display current week's profit/loss separately from total P&L.

#### Scenario: Weekly P&L display
- **WHEN** portfolio summary is rendered in frontend
- **THEN** weekly P&L is shown as a separate row below total assets, with sub-items colored red/green
