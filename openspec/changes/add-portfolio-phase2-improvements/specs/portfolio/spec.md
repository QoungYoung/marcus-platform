## MODIFIED Requirements

### Requirement: Account Summary
The system SHALL return current account status including total assets, available cash, market value, total P&L, and sector concentration breakdown.

#### Scenario: Get portfolio summary
- **WHEN** GET /api/v1/portfolio is called
- **THEN** response includes total_assets, available_cash, market_value, total_pl, today_pl, weekly_pl, and `sector_concentration` containing sector weight breakdown when positions exist
