## MODIFIED Requirements

### Requirement: Place Trade
The system SHALL accept trade requests and execute them through the active trading engine backend (VN.PY PaperAccountApp or legacy PaperTradingEngine, determined by ENGINE_BACKEND).

#### Scenario: Buy order via VN.PY backend
- **WHEN** POST /api/v1/trades with action=buy, symbol, quantity, price is called and ENGINE_BACKEND is vnpy
- **THEN** order is executed via VN.PY OmsEngine, account cash and position are updated atomically, and events are synced to PostgreSQL

#### Scenario: Sell order via VN.PY backend
- **WHEN** POST /api/v1/trades with action=sell, symbol, quantity, price is called and ENGINE_BACKEND is vnpy
- **THEN** order is executed via VN.PY OmsEngine, position is reduced via FIFO, and trade record with accurate profit is created

#### Scenario: Buy order via legacy backend
- **WHEN** POST /api/v1/trades with action=buy is called and ENGINE_BACKEND is paper
- **THEN** order is executed via legacy PaperTradingEngine (existing behavior preserved)

### Requirement: Void Trade
The system SHALL support voiding (cancelling) a trade that was made in error, with the void operation correctly reversing the VN.PY account state.

#### Scenario: Void a VN.PY trade
- **WHEN** POST /api/v1/trades/void with trade_id is called and the trade was executed via VN.PY
- **THEN** the trade is marked as voided in PostgreSQL, and the account's available_cash and position state are corrected to reflect the reversal
