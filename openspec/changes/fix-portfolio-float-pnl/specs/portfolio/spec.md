## MODIFIED Requirements

### Requirement: Account Summary
The system SHALL return current account status including total assets, available cash, market value, realised P&L, float P&L, and total P&L.

#### Scenario: Get portfolio summary
- **WHEN** GET /api/v1/portfolio is called
- **THEN** response includes total_assets, available_cash, market_value, realized_pnl, float_pnl, total_pnl, position_ratio, week_pl, total_return, and total_return_pct

#### Scenario: Float P&L equals sum of position floats
- **WHEN** portfolio summary is computed
- **THEN** `account.float_pnl` SHALL equal the sum of all positions' `floating_pnl`
- **AND** `account.float_pnl` SHALL NOT be derived from `total_pnl - realized_pnl`

## ADDED Requirements

### Requirement: Float P&L Calculation
The system SHALL calculate floating P&L as the sum of each position's `market_value - cost_value`, where `cost_value = volume * avg_price`.

#### Scenario: Single position float P&L
- **WHEN** there is exactly one position with market_value 190,000 and cost_value 200,000
- **THEN** `account.float_pnl` SHALL equal -10,000

#### Scenario: No positions
- **WHEN** there are zero positions
- **THEN** `account.float_pnl` SHALL equal 0
