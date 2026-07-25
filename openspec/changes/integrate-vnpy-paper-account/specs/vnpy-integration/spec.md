## ADDED Requirements

### Requirement: VN.PY Event Engine Lifecycle
The system SHALL start the VN.PY MainEngine with PaperAccountApp as a singleton daemon thread during FastAPI lifespan startup, and SHALL stop it during lifespan shutdown.

#### Scenario: Engine starts on application boot
- **WHEN** the FastAPI application starts via lifespan
- **THEN** VN.PY MainEngine is initialized with PaperAccountApp, event engine thread is running, and the bridge instance is stored in `app.state.vnpy_bridge`

#### Scenario: Engine stops on application shutdown
- **WHEN** the FastAPI application shuts down
- **THEN** all VN.PY event listeners are unregistered, event engine thread is joined, and resources are released

#### Scenario: Engine is a singleton
- **WHEN** multiple callers request the VN.PY bridge instance
- **THEN** all callers receive the same singleton instance, sharing the same event engine and account state

### Requirement: Order Execution via VN.PY
The system SHALL route buy and sell orders through VN.PY's OmsEngine.send_order, and SHALL wait synchronously for the order to be filled (or timeout after 5 seconds).

#### Scenario: Buy order executed
- **WHEN** `VNPyBridge.send_order(symbol, Direction.LONG, price, volume)` is called with sufficient funds
- **THEN** VN.PY PaperAccountApp matches the order, updates account cash and position, fires TradeEvent, and returns the order ID

#### Scenario: Sell order rejected due to insufficient position
- **WHEN** `VNPyBridge.send_order(symbol, Direction.SHORT, price, volume)` is called but position volume is insufficient
- **THEN** VN.PY rejects the order, and the bridge returns an error without modifying account state

#### Scenario: Order timeout
- **WHEN** an order is submitted but does not receive a fill event within 5 seconds
- **THEN** the bridge returns a timeout error and the order is cancelled

### Requirement: PostgreSQL Event Sync
The system SHALL register VN.PY event listeners that write OrderEvent, TradeEvent, AccountEvent, and PositionEvent data to the corresponding PostgreSQL tables in real-time.

#### Scenario: Trade event synced to paper_trades
- **WHEN** VN.PY fires a TradeEvent after matching an order
- **THEN** a row is inserted into `paper_trades` with the correct orderid, symbol, direction, price, volume, amount, and VN.PY-calculated profit

#### Scenario: Account event synced to paper_account_info
- **WHEN** VN.PY fires an AccountEvent after cash changes
- **THEN** the `paper_account_info` row is updated with the new available_cash, frozen_cash values

#### Scenario: Position event synced to paper_positions
- **WHEN** VN.PY fires a PositionEvent after a trade alters a position
- **THEN** `paper_positions` is upserted with the current volume, frozen, avg_price, and entry_date. If volume reaches zero, the position row is deleted.

### Requirement: ENGINE_BACKEND Configuration Switch
The system SHALL support an `ENGINE_BACKEND` environment variable to switch between VN.PY ("vnpy") and the legacy paper engine ("paper"), with "vnpy" as the default.

#### Scenario: VN.PY backend selected
- **WHEN** `ENGINE_BACKEND=vnpy` (or not set)
- **THEN** `VNPyBridge` is used for all trade execution and account queries

#### Scenario: Legacy backend selected
- **WHEN** `ENGINE_BACKEND=paper`
- **THEN** the existing `PaperTradingEngine` is used instead, providing a fallback during migration
