## ADDED Requirements

### Requirement: Concept-name fallback reads from PostgreSQL
The system SHALL resolve concept-name standardization fallback queries from the PostgreSQL `stock_concept_map` table instead of the SQLite `data/stock_pool.db` file.

#### Scenario: Vocabulary miss falls back to PostgreSQL
- **WHEN** a raw concept name does not match the in-memory vocabulary in `core/news_analyzer.py`
- **THEN** the fallback SHALL query `SELECT DISTINCT concept_name FROM stock_concept_map` against PostgreSQL, and on failure SHALL return the raw concept name unchanged

### Requirement: Catalyst tracker stock-name batch lookup reads from PostgreSQL
The system SHALL batch-resolve stock names in `apps/news/news_catalyst_tracker.py` from the PostgreSQL `stock_pool` table.

#### Scenario: Batch catalyst update resolves names from PostgreSQL
- **WHEN** `batch_update_catalysts()` runs with a non-empty code list
- **THEN** names SHALL be looked up via `SELECT symbol, name FROM stock_pool WHERE symbol IN (...)` against PostgreSQL, and codes not found SHALL still fall back to Xueqiu

### Requirement: Direction-data dump reads the stock pool from PostgreSQL
The system SHALL build the direction feature-dump stock pool from the PostgreSQL `stock_pool` table.

#### Scenario: dump_direction_data builds the pool from PostgreSQL
- **WHEN** `scripts/dump_direction_data.py` runs
- **THEN** its stock pool SHALL be loaded with `SELECT ts_code, symbol, name, industry FROM stock_pool WHERE is_st = 0 AND industry IS NOT NULL AND industry != ''` against PostgreSQL

### Requirement: No direct SQLite reads of stock_pool.db remain in the migrated readers
The system SHALL NOT open `data/stock_pool.db` directly in `core/news_analyzer.py`, `apps/news/news_catalyst_tracker.py`, or `scripts/dump_direction_data.py`.

#### Scenario: Grep finds no SQLite stock_pool reads
- **WHEN** the codebase is scanned for `sqlite3.connect` against `stock_pool.db` in those three modules
- **THEN** no such read paths SHALL remain for stock pool / concept map data
