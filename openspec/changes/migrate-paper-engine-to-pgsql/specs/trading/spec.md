## MODIFIED Requirements

### Requirement: Place Trade
The system SHALL accept trade requests and execute them through MarcusVNPyExecutor, persisting all trade data to PostgreSQL.

#### Scenario: Buy order
- **WHEN** POST /api/v1/trades with action=buy, symbol, quantity, price is called
- **THEN** order is executed via VNPy paper engine and trade record is created in PostgreSQL `trades` table

#### Scenario: Sell order
- **WHEN** POST /api/v1/trades with action=sell, symbol, quantity, price is called
- **THEN** order is executed and position is reduced accordingly in PostgreSQL

### Requirement: Trade History
The system SHALL return paginated trade history from PostgreSQL `trades` table.

#### Scenario: Get trade history
- **WHEN** GET /api/v1/trades/history is called
- **THEN** response includes trade_id, symbol, action, quantity, price, reason, timestamp for each trade queried from PostgreSQL

### Requirement: Void Trade
The system SHALL support voiding a trade via PostgreSQL update.

#### Scenario: Void a trade
- **WHEN** POST /api/v1/trades/void with trade_id is called
- **THEN** the trade's `voided` column in PostgreSQL `trades` table is set to 1 and portfolio state is corrected

### Requirement: Trade Reason
The system SHALL store and display the reason for each trade execution in PostgreSQL.

#### Scenario: Trade reason preserved
- **WHEN** a trade is executed with a reason field
- **THEN** the reason is persisted in PostgreSQL `trades.reason` column and visible in trade history
