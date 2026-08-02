## Purpose

Trade execution and history management — placing orders, viewing trade history, voiding trades, and tracking execution reasons.

## Requirements

### Requirement: Place Trade
The system SHALL accept trade requests and execute them through MarcusVNPyExecutor.

#### Scenario: Buy order
- **WHEN** POST /api/v1/trades with action=buy, symbol, quantity, price is called
- **THEN** order is executed via VNPy paper engine and trade record is created

#### Scenario: Sell order
- **WHEN** POST /api/v1/trades with action=sell, symbol, quantity, price is called
- **THEN** order is executed and position is reduced accordingly

### Requirement: Trade History
The system SHALL return paginated trade history with all trade details.

#### Scenario: Get trade history
- **WHEN** GET /api/v1/trades/history is called
- **THEN** response includes trade_id, symbol, action, quantity, price, reason, timestamp for each trade

### Requirement: Void Trade
The system SHALL support voiding (cancelling) a trade that was made in error.

#### Scenario: Void a trade
- **WHEN** POST /api/v1/trades/void with trade_id is called
- **THEN** the trade is marked as voided and portfolio state is corrected

### Requirement: Trade Reason
The system SHALL store and display the reason for each trade execution.

#### Scenario: Trade reason preserved
- **WHEN** a trade is executed with a reason field
- **THEN** the reason is persisted and visible in trade history
