## 1. Environment Setup

- [x] 1.1 Add `vnpy>=4.0` and `vnpy_paperaccount` to `apps/paper-trading/requirements.txt`
- [x] 1.2 Install dependencies and verify `from vnpy.trader.engine import MainEngine` works
- [x] 1.3 Add `ENGINE_BACKEND` config to `backend/app/config.py` Settings class (default: "vnpy")

## 2. VNPyBridge Core

- [x] 2.1 Create `backend/app/core/trading/vnpy_bridge.py` with `VNPyBridge` class skeleton
- [x] 2.2 Implement `VNPyBridge.start()` — initialize MainEngine, register PaperAccountApp, start event engine in daemon thread
- [x] 2.3 Implement `VNPyBridge.stop()` — unregister listeners, stop event engine, join thread
- [x] 2.4 Implement `VNPyBridge.send_order(symbol, direction, price, volume, reason)` — wrap MainEngine.send_order with synchronous wait for fill event (5s timeout)
- [x] 2.5 Implement `VNPyBridge.get_account()` — return dict with available_cash, frozen_cash, initial_capital, position_value, total_asset, realized_pnl, float_pnl, total_pnl
- [x] 2.6 Implement `VNPyBridge.get_positions()` — return list of position dicts with symbol, volume, frozen, avg_price, entry_date
- [x] 2.7 Implement `VNPyBridge.get_trades(symbol, limit)` — query PostgreSQL paper_trades for trade history
- [x] 2.8 Implement `VNPyBridge.get_orders(symbol, status, limit)` — query PostgreSQL paper_orders

## 3. PostgreSQL Event Sync

- [x] 3.1 Create `backend/app/core/trading/vnpy_listeners.py` with event listener classes for OrderEvent, TradeEvent, AccountEvent, PositionEvent
- [x] 3.2 Implement OrderEventListener — on EVENT_ORDER, upsert into paper_orders table
- [x] 3.3 Implement TradeEventListener — on EVENT_TRADE, insert into paper_trades table with VN.PY-calculated profit
- [x] 3.4 Implement AccountEventListener — on EVENT_ACCOUNT, update paper_account_info with new balance
- [x] 3.5 Implement PositionEventListener — on EVENT_POSITION, upsert paper_positions with full columns (symbol, volume, frozen, avg_price, entry_date, highest_price); delete row when volume reaches 0
- [x] 3.6 Register all listeners in VNPyBridge.start() and unregister in stop()

## 4. Database Migration

- [x] 4.1 Add migration SQL: ALTER TABLE paper_positions ADD COLUMN volume, frozen, avg_price (with defaults)
- [x] 4.2 Create `scripts/migrate_to_vnpy.py` — read current account state from paper_account_info, compute current positions from paper_trades FIFO, print migration plan
- [x] 4.3 Add `--execute` flag to migration script to apply: seed VN.PY account with initial_capital, available_cash, and positions
- [x] 4.4 Add migration verification step: compare VN.PY total_asset with legacy paper_daily_snapshot.total_asset

## 5. MarcusVNPyExecutor Refactor

- [x] 5.1 Make MarcusVNPyExecutor accept VNPyBridge as constructor parameter (or fetch from app.state)
- [x] 5.2 Rewrite `buy()` / `sell()` to call `self.bridge.send_order()` instead of `self.engine.buy()` / `self.engine.sell()`
- [x] 5.3 Rewrite `get_account()` to use VNPyBridge.get_account() as primary source, fall back to paper_engine
- [x] 5.4 Rewrite `get_positions()` and `get_trades()` similarly
- [x] 5.5 Preserve all risk check logic (回撤熔断, T+1, 连续亏损) in MarcusVNPyExecutor — these remain unchanged

## 6. FastAPI Lifespan Integration

- [x] 6.1 In `backend/app/main.py` lifespan startup: create VNPyBridge singleton, start it, store in app.state
- [x] 6.2 Inject bridge into StopLossMonitor, PositionTierMonitor, CandidatePoolMonitor, LongTermPoolMonitor constructors
- [x] 6.3 In lifespan shutdown: stop all monitors first, then stop bridge
- [x] 6.4 Update `backend/app/api/trades.py` — fetch executor with bridge from app.state
- [x] 6.5 Update `backend/app/api/scheduler.py` — same bridge injection for all monitor start/stop endpoints

## 7. Backward Compatibility

- [x] 7.1 Add `get_bridge()` factory that reads ENGINE_BACKEND env var: returns VNPyBridge or PaperTradingEngine
- [x] 7.2 Ensure `ENGINE_BACKEND=paper` path still works for all trade/portfolio operations
- [x] 7.3 Add `backend/app/api/portfolio.py` adaptation: read position data from `paper_positions` new columns when available

## 8. Testing & Validation

- [x] 8.1 Write unit test: VNPyBridge.start() → send_order(buy) → get_account() → send_order(sell) → verify P&L
- [x] 8.2 Write unit test: verify PositionEventListener deletes row when volume reaches 0
- [x] 8.3 Write unit test: verify AccountEventListener updates paper_account_info cash correctly
- [x] 8.4 Manual validation: run with ENGINE_BACKEND=vnpy, execute a full buy-sell cycle via API, verify /portfolio response
- [x] 8.5 Run migration script against production database copy, verify total_asset matches legacy snapshot
