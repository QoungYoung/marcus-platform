## MODIFIED Requirements

### Requirement: Account Summary
The system SHALL return current account status including total assets, available cash, market value, and total P&L, scoped to an account.

#### Scenario: Get portfolio summary for default account
- **WHEN** GET /api/v1/portfolio is called without account
- **THEN** response includes total_assets, available_cash, market_value, total_pl, today_pl, weekly_pl for the `stock` account

#### Scenario: Get portfolio summary for golden pit account
- **WHEN** GET /api/v1/portfolio?account=golden_pit is called
- **THEN** response includes the same summary fields computed from the `golden_pit` account only

### Requirement: Position List
The system SHALL return all current positions with cost basis, current price, and unrealized P&L, scoped to an account.

#### Scenario: Get positions for an account
- **WHEN** GET /api/v1/portfolio/positions?account=golden_pit is called
- **THEN** each position belongs to the `golden_pit` account and includes symbol, name, quantity, avg_cost, current_price, unrealized_pl, unrealized_pl_pct

#### Scenario: Positions do not leak across accounts
- **WHEN** GET /api/v1/portfolio/positions?account=stock is called
- **THEN** response SHALL NOT include any position owned by another account

### Requirement: Equity History
The system SHALL return historical equity curve data points scoped to an account.

#### Scenario: Get equity history for an account
- **WHEN** GET /api/v1/portfolio/equity?account=golden_pit is called
- **THEN** response includes list of equity points computed from the `golden_pit` account snapshots only

### Requirement: Weekly P&L
The system SHALL calculate and display current week's profit/loss separately from total P&L, within the selected account.

#### Scenario: Weekly P&L display
- **WHEN** portfolio summary is rendered in frontend for a selected account
- **THEN** weekly P&L is shown as a separate row below total assets, with sub-items colored red/green, computed from that account

## ADDED Requirements

### Requirement: Account Switching
The system SHALL provide an account list endpoint and a frontend account switcher so users can view each paper account's portfolio independently.

#### Scenario: List available accounts
- **WHEN** GET /api/v1/accounts is called
- **THEN** response includes account_id, name, module, initial_capital, available_cash for all enabled accounts

#### Scenario: Switch account in Portfolio page
- **WHEN** user selects `golden_pit` in the Portfolio page account switcher
- **THEN** the page SHALL reload summary, positions and equity for the `golden_pit` account
- **THEN** the page SHALL show the selected account name in the header
