## ADDED Requirements

### Requirement: Monitor Log Table
The system SHALL create a PostgreSQL table `position_add_monitor_log` to persist position-add monitoring check results.

#### Scenario: Table schema
- **WHEN** database is initialized
- **THEN** `position_add_monitor_log` table exists with columns: id (SERIAL PK), timestamp (TIMESTAMPTZ), symbol (VARCHAR(16)), current_tier (VARCHAR(10)), target_tier (VARCHAR(10)), action (VARCHAR(20)), result (VARCHAR(20)), float_pnl_pct (FLOAT), current_price (FLOAT), add_shares (INTEGER), block_reason (VARCHAR(500)), gate_details (JSONB), trend_details (JSONB)

### Requirement: Persist Check Result Per Position
The system SHALL write one row to `position_add_monitor_log` for each position after every monitoring check cycle.

#### Scenario: Hold result logged
- **WHEN** a position's float PnL does not meet tier upgrade threshold
- **THEN** a row is written with result='HOLD', action='HOLD', and the evaluation signal as block_reason

#### Scenario: Blocked result logged
- **WHEN** a position triggers tier upgrade but fails gate arbitration
- **THEN** a row is written with result='BLOCKED', target_tier set, block_reason containing the failed gate names, and gate_details JSONB containing all gate check results

#### Scenario: Executed result logged
- **WHEN** a position passes all gates and add-position order is executed
- **THEN** a row is written with result='EXECUTED', add_shares set to the executed share count, and gate_details containing all gate check results

#### Scenario: Outflow result logged
- **WHEN** a position's main capital flow is net outflow
- **THEN** a row is written with result='OUTFLOW' and block_reason containing the outflow amount

#### Scenario: Skipped result logged
- **WHEN** a position passes gate but execution fails (e.g., insufficient cash)
- **THEN** a row is written with result='SKIPPED' and block_reason explaining the failure

#### Scenario: Write failure resilient
- **WHEN** database write fails
- **THEN** the monitoring cycle continues, error is logged, and the failure does not interrupt the next position's check

### Requirement: Gate Details Storage
The system SHALL store complete gate arbitration results in JSONB format for detail display.

#### Scenario: Gate details structure
- **WHEN** gate arbitration runs for a position
- **THEN** gate_details JSONB contains an array of objects, each with keys: gate (string), status (string: 'PASSED'/'BLOCKED'/'SKIPPED'/'DOWNGRADE'), detail (string)

### Requirement: Trend Details Storage
The system SHALL store complete trend strength check results in JSONB format for detail display.

#### Scenario: Trend details structure
- **WHEN** trend strength check runs for a position
- **THEN** trend_details JSONB contains: core_passed (bool), aux_passed (int), aux_total (int), checks (object with keys ma_align, ma5_slope, volume_ratio, sector_flow, moneyflow, each containing passed, value, threshold, detail)

### Requirement: Query Monitor Logs API
The system SHALL provide an API endpoint to query monitor logs with filtering, sorting, and pagination.

#### Scenario: List logs with filters
- **WHEN** GET /api/v1/monitor/logs is called with optional query params: symbol, result, date_from, date_to, page, page_size
- **THEN** response returns paginated list of monitor log rows (without gate_details and trend_details JSONB to reduce payload)

#### Scenario: Default sort order
- **WHEN** GET /api/v1/monitor/logs is called without sort param
- **THEN** results are sorted by timestamp descending

### Requirement: Query Monitor Log Detail API
The system SHALL provide an API endpoint to get full detail of a single monitor log row, including JSONB fields.

#### Scenario: Get log detail
- **WHEN** GET /api/v1/monitor/logs/{log_id} is called
- **THEN** response includes all columns including full gate_details and trend_details JSONB
