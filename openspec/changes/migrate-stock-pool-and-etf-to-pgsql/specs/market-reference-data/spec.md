## ADDED Requirements

### Requirement: Stock pool master data persisted in PostgreSQL
The system SHALL persist the stock pool master data (stock_pool, sectors, concept_sectors, stock_concept_map, stock_sector_map) in PostgreSQL instead of the SQLite `data/stock_pool.db` file.

#### Scenario: Tables created idempotently in PostgreSQL
- **WHEN** the migration script or backend initialization runs
- **THEN** the 5 tables (`stock_pool`, `sectors`, `concept_sectors`, `stock_concept_map`, `stock_sector_map`) SHALL exist in PostgreSQL with the same primary keys, unique constraints, and indexes as the SQLite source

#### Scenario: All rows migrated from SQLite
- **WHEN** the migration script completes without `--dry-run`
- **THEN** every row from the SQLite source tables SHALL exist in the corresponding PostgreSQL table, and per-table inserted row counts SHALL be reported

#### Scenario: Stock pool refresh writes to PostgreSQL
- **WHEN** `StockPoolManager` refreshes stock/industry/concept data (e.g. via `jobs/stock_pool_manager.py`)
- **THEN** the data SHALL be upserted into PostgreSQL with `ON CONFLICT` semantics and no direct SQLite connection is used

### Requirement: ETF pool persisted in PostgreSQL
The system SHALL persist the ETF pool (`etf_pool`) in PostgreSQL instead of the `etf_pool` table inside the SQLite `data/cache.db` file.

#### Scenario: ETF sync writes to PostgreSQL
- **WHEN** `XueqiuEngine.sync_etf_pool()` runs (via `jobs/stock_pool_manager.py` or `POST /api/v1/etf/sync`)
- **THEN** ETF pool rows SHALL be inserted or replaced in the PostgreSQL `etf_pool` table

#### Scenario: ETF pool reads come from PostgreSQL
- **WHEN** `XueqiuEngine.get_etf_pool_from_db()` is called (used by `/api/v1/market/search` and `/api/v1/etf/*`)
- **THEN** the results SHALL be read from the PostgreSQL `etf_pool` table

### Requirement: All readers use PostgreSQL
The system SHALL read stock pool and ETF pool data through PostgreSQL for all backend APIs and jobs; no direct `sqlite3.connect` to `stock_pool.db` or `cache.db` SHALL remain for these datasets.

#### Scenario: Market search reads from PostgreSQL
- **WHEN** `GET /api/v1/market/search` is called
- **THEN** stock results SHALL be queried from the PostgreSQL `stock_pool` table and ETF results from the PostgreSQL `etf_pool` table

#### Scenario: Stock name and industry lookup reads from PostgreSQL
- **WHEN** any API or job resolves a stock name or industry (e.g. `portfolio.py`, `trades.py`, `news.py`, `market_scan.py`, `fund_flow.py`)
- **THEN** the lookup SHALL be served from the PostgreSQL `stock_pool` table

#### Scenario: Concept and sector boards read from PostgreSQL
- **WHEN** concept/sector board queries execute (e.g. `industry_leaderboard.py`, `market.py` concept boards, `local_data_provider.py`)
- **THEN** they SHALL join `stock_concept_map` / `stock_sector_map` / `sectors` / `concept_sectors` in PostgreSQL

### Requirement: Data migration script
The system SHALL provide a migration script that copies `stock_pool.db` and the `etf_pool` table of `cache.db` into PostgreSQL.

#### Scenario: Dry-run reports counts only
- **WHEN** the script is executed with `--dry-run`
- **THEN** it SHALL print the source SQLite row counts and the target table mapping without writing any data

#### Scenario: Normal run is idempotent and preserves source files
- **WHEN** the script is executed normally
- **THEN** it SHALL clear the target PostgreSQL tables, copy all rows from the SQLite sources, reset sequences if applicable, and SHALL NOT delete the SQLite source files

## REMOVED Requirements

### Requirement: SQLite as runtime data source for stock pool and ETF pool
**Reason**: The runtime data source moves to PostgreSQL; the SQLite files remain only as a one-time migration source and backup.
**Migration**: Run `scripts/migrate_market_data_to_pgsql.py` before deploying the new code; keep `data/stock_pool.db` and `data/cache.db` as backups.