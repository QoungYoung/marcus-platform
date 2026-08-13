## MODIFIED Requirements

### Requirement: Place Trade
The system SHALL accept trade requests and execute them through MarcusVNPyExecutor, scoped to an account.

#### Scenario: Buy order (default account)
- **WHEN** POST /api/v1/trades with action=buy, symbol, quantity, price is called without account
- **THEN** order is executed via VNPy paper engine on the `stock` account and trade record is created

#### Scenario: Buy order (specified account)
- **WHEN** POST /api/v1/trades with action=buy, account=golden_pit, symbol, quantity, price is called
- **THEN** order is executed on the `golden_pit` account and trade record is created under that account

#### Scenario: Sell order
- **WHEN** POST /api/v1/trades with action=sell, account, symbol, quantity, price is called
- **THEN** order is executed and the position in that account is reduced accordingly

#### Scenario: Unknown account rejected
- **WHEN** POST /api/v1/trades with an account_id not present in paper_accounts is called
- **THEN** the request SHALL be rejected with an error and no trade record is created

### Requirement: Trade History
The system SHALL return paginated trade history for a given account, with all trade details.

#### Scenario: Get trade history for default account
- **WHEN** GET /api/v1/trades/history is called without account
- **THEN** response includes trades of the `stock` account only, with trade_id, symbol, action, quantity, price, reason, timestamp for each trade

#### Scenario: Get trade history for golden pit account
- **WHEN** GET /api/v1/trades/history?account=golden_pit is called
- **THEN** response includes only trades belonging to the `golden_pit` account

### Requirement: Void Trade
The system SHALL support voiding (cancelling) a trade that was made in error, within the trade's own account.

#### Scenario: Void a trade
- **WHEN** POST /api/v1/trades/void with trade_id is called
- **THEN** the trade is marked as voided and the owning account's portfolio state is corrected

### Requirement: Trade Reason
The system SHALL store and display the reason for each trade execution.

#### Scenario: Trade reason preserved
- **WHEN** a trade is executed with a reason field
- **THEN** the reason is persisted and visible in trade history
