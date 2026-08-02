## ADDED Requirements

### Requirement: Generic query API backed by PostgreSQL
The system SHALL serve the generic database query endpoints (`GET /api/v1/db/query`, `GET /api/v1/db/schema/{db}`) from PostgreSQL for migrated datasets, while keeping the existing request/response contract (params `db`, `table`, `columns`, `where`, `order_by`, `limit`; response `rows` + `columns`).

#### Scenario: Query stock pool from PostgreSQL
- **WHEN** `GET /api/v1/db/query?db=stock_pool&table=stock_pool&limit=10` is called
- **THEN** the rows SHALL be selected from the PostgreSQL `stock_pool` table and returned in the same `{rows, columns}` shape

#### Scenario: Schema reflects PostgreSQL tables
- **WHEN** `GET /api/v1/db/schema/stock_pool` is called
- **THEN** the returned schema SHALL describe the PostgreSQL tables (`stock_pool`, `sectors`, `concept_sectors`, `stock_concept_map`, `stock_sector_map`) with their PG column names and types

### Requirement: news.db remains SQLite and read-only through the db API
The system SHALL keep `news.db` on SQLite (no data migration) and SHALL continue serving `news` queries from the SQLite file through the db API.

#### Scenario: News queries still read the SQLite file
- **WHEN** `GET /api/v1/db/query?db=news&table=news&limit=10` is called
- **THEN** the rows SHALL be read from `data/news.db`

#### Scenario: Write operations are restricted to legacy SQLite datasets
- **WHEN** `POST /api/v1/db/write` is called with a `db` value that maps to a PostgreSQL-backed dataset (e.g. `stock_pool`)
- **THEN** the request SHALL be rejected with HTTP 400 and a clear message that PG-backed datasets are read-only through this API
- **AND WHEN** `POST /api/v1/db/write` is called with a legacy SQLite dataset (e.g. `news`)
- **THEN** the write SHALL be executed against the SQLite file as before