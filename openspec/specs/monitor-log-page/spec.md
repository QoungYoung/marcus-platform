## Purpose

Frontend page for viewing position-add monitoring logs — displays persisted check results with filtering, pagination, and drill-down detail.

## Requirements

### Requirement: Monitor Log Page Route
The system SHALL provide a frontend page at `/monitor` for viewing position-add monitoring logs.

#### Scenario: Page accessible from navigation
- **WHEN** user clicks "监控日志" in TopNav
- **THEN** browser navigates to `/monitor` and displays the monitor log list page

#### Scenario: Page shows loading state
- **WHEN** the page is first loaded and API data is being fetched
- **THEN** skeleton placeholders are displayed until data arrives

### Requirement: Monitor Log List with Filters
The system SHALL display a paginated table of monitor log entries with filter controls.

#### Scenario: Default list
- **WHEN** page loads without any filter
- **THEN** the most recent 20 log entries are displayed, sorted by timestamp descending

#### Scenario: Filter by symbol
- **WHEN** user enters a stock symbol in the symbol filter input
- **THEN** the list refreshes showing only logs for that symbol

#### Scenario: Filter by result type
- **WHEN** user selects a result type from the dropdown (HOLD/BLOCKED/EXECUTED/SKIPPED/OUTFLOW)
- **THEN** the list refreshes showing only logs with that result

#### Scenario: Filter by date range
- **WHEN** user selects date_from and/or date_to
- **THEN** the list refreshes showing only logs within that date range

#### Scenario: Pagination
- **WHEN** total log count exceeds page_size
- **THEN** pagination controls appear, allowing navigation between pages

#### Scenario: Empty state
- **WHEN** no logs match the current filters
- **THEN** an empty-state placeholder is displayed

### Requirement: Result Badge Display
The system SHALL display each log entry's result with a color-coded badge.

#### Scenario: Badge colors
- **WHEN** a log entry is rendered
- **THEN** EXECUTED shows green badge, BLOCKED shows red badge, HOLD shows grey badge, SKIPPED shows yellow badge, OUTFLOW shows blue badge

### Requirement: Log Detail Expansion
The system SHALL allow users to view full gate and trend details for a single log entry.

#### Scenario: Expand row for details
- **WHEN** user clicks on a log row
- **THEN** the row expands to show gate_details (list of gates with PASSED/BLOCKED/SKIPPED status) and trend_details (core_passed, aux_passed/aux_total, individual check values)

#### Scenario: Collapse row
- **WHEN** user clicks on an already-expanded row
- **THEN** the detail section collapses

#### Scenario: Detail fetch on expand
- **WHEN** a row is expanded
- **THEN** the detail JSONB data is fetched from GET /api/v1/monitor/logs/{log_id}
