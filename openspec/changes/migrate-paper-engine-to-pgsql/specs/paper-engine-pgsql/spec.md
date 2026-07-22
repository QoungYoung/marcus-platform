## ADDED Requirements

### Requirement: Paper engine persists to PostgreSQL
The system SHALL persist all paper trading data (orders, trades, positions metadata, account state, daily snapshots) to PostgreSQL instead of SQLite.

#### Scenario: Engine initializes PostgreSQL tables on startup
- **WHEN** `PaperTradingEngine.__init__` is called
- **AND** the required tables do not exist in PostgreSQL
- **THEN** the engine SHALL execute CREATE TABLE IF NOT EXISTS for all 5 tables

#### Scenario: Engine loads account state from PostgreSQL
- **WHEN** `PaperTradingEngine.__init__` is called
- **THEN** `available_cash`, `frozen_cash`, `initial_capital` SHALL be loaded from the `account_info` table in PostgreSQL

#### Scenario: Engine loads positions from PostgreSQL
- **WHEN** `PaperTradingEngine.__init__` is called
- **THEN** positions SHALL be reconstructed via FIFO replay from the `trades` table in PostgreSQL

### Requirement: Concurrent-safe account cash updates
The system SHALL use `SELECT ... FOR UPDATE` row-level locking when updating `available_cash` or `frozen_cash` in the `account_info` table, ensuring no write-write conflicts between concurrent engine instances.

#### Scenario: Buy order freezes cash under row lock
- **WHEN** `PaperTradingEngine.buy()` is called
- **THEN** the engine SHALL begin a transaction, lock the `account_info` row with `SELECT ... FOR UPDATE`, deduct `available_cash` and increase `frozen_cash`, then commit

#### Scenario: Match order updates cash under row lock
- **WHEN** `PaperTradingEngine.match_order()` is called
- **THEN** all account state changes (cash update, position update, trade insert) SHALL occur within a single transaction with `account_info` row locked

#### Scenario: Concurrent buy requests do not corrupt cash
- **WHEN** two engine instances simultaneously execute `buy()` for different symbols
- **THEN** each instance SHALL acquire the row lock sequentially, ensuring `available_cash` remains consistent

### Requirement: SQLite-to-PostgreSQL migration script
The system SHALL provide a migration script that copies all data from the existing SQLite `trades.db` to PostgreSQL tables.

#### Scenario: Migration preserves all trade history
- **WHEN** `scripts/migrate_sqlite_to_pgsql.py` is executed
- **THEN** all rows from SQLite `orders`, `trades`, `positions`, `account_info`, `daily_snapshot` tables are inserted into the corresponding PostgreSQL tables without data loss

#### Scenario: Migration is idempotent
- **WHEN** the migration script is run multiple times
- **THEN** it SHALL clear existing data in target tables before re-inserting (to avoid duplicates)
