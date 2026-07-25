## MODIFIED Requirements

### Requirement: Account Summary
The system SHALL return current account status including total assets, available cash, frozen cash, market value, realized P&L, float P&L, and total P&L, sourced from the PostgreSQL tables synced by the active trading engine.

#### Scenario: Get portfolio summary from VN.PY-synced data
- **WHEN** GET /api/v1/portfolio is called and ENGINE_BACKEND is vnpy
- **THEN** response includes total_assets, available_cash, frozen_cash, market_value, realized_pl, float_pl, total_pl — all values derived from VN.PY-synced paper_account_info and paper_positions tables, with realized_pl matching the sum of trade profits exactly

### Requirement: Position List
The system SHALL return all current positions with cost basis, current price, and unrealized P&L, sourced from the paper_positions table which now contains authoritative volume, frozen, and avg_price data.

#### Scenario: Get positions with accurate volume and cost
- **WHEN** GET /api/v1/portfolio/positions is called
- **THEN** each position includes symbol, name, quantity, frozen, avg_cost from paper_positions (no FIFO recalculation needed), and the position list exactly matches VN.PY's internal position state

#### Scenario: Fully sold position removed
- **WHEN** a position's volume reaches zero after a sell trade
- **THEN** the position row is deleted from paper_positions and no longer appears in GET /api/v1/portfolio/positions
