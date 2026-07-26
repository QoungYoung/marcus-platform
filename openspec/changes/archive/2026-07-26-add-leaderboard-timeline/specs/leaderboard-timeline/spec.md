## ADDED Requirements

### Requirement: API supports historical date query
`GET /market/industry-leaderboard` SHALL accept an optional `date` query parameter (format `YYYYMMDD`). When provided, the service MUST use Tushare historical data (daily, stk_factor, moneyflow) exclusively instead of real-time sources. When omitted, the existing real-time behavior SHALL remain unchanged.

#### Scenario: Historical date query
- **WHEN** client requests `GET /market/industry-leaderboard?date=20260722`
- **THEN** backend returns leaderboard data reconstructed from Tushare historical data for 2026-07-22
- **AND** `data_source` field is set to "tushare"

#### Scenario: Real-time query unchanged
- **WHEN** client requests `GET /market/industry-leaderboard` without date parameter
- **THEN** backend uses existing real-time data sources (Tencent quotes, Tushare stk_factor, East Money)
- **AND** behavior is identical to current implementation

#### Scenario: Invalid date rejection
- **WHEN** client requests date in the future or non-trading day
- **THEN** backend returns empty items list with appropriate warning

### Requirement: Forward returns endpoint
`GET /market/forward-returns/{symbol}?date=YYYYMMDD` SHALL return forward-looking performance data for the specified stock and historical date. The response MUST include next-day return, 3-day cumulative return, 5-day cumulative return, and an array of 10 daily close prices after the given date for sparkline rendering.

#### Scenario: Forward returns for past date
- **WHEN** client requests `GET /market/forward-returns/600519.SH?date=20260720`
- **THEN** response includes `next_day_pct`, `day3_pct`, `day5_pct` as percentage floats
- **AND** response includes `sparkline_closes: [float, ...]` with 10 post-date daily close prices
- **AND** `benchmark_date` confirms the requested date

#### Scenario: Forward returns for latest trading day
- **WHEN** client requests forward returns for the latest trading day
- **THEN** response indicates data not yet available with `available: false`

### Requirement: Historical date permanent caching
Leaderboard queries with a historical `date` parameter SHALL be cached permanently. Queries without date (real-time mode) SHALL retain the existing 60-second TTL.

#### Scenario: Historical cache hit
- **WHEN** client requests same historical date and filters twice
- **THEN** second request returns cached result with no Tushare API calls

#### Scenario: Real-time cache unchanged
- **WHEN** client requests real-time data (no date parameter)
- **THEN** cache TTL remains 60 seconds

### Requirement: Timeline date navigation
The frontend SHALL display a horizontal scrollable timeline bar showing the 20 most recent trading days. Users MUST be able to click a date pill to switch the leaderboard to that date's rankings. The currently selected date pill SHALL be visually highlighted.

#### Scenario: Click historical date
- **WHEN** user clicks a date pill for a past trading day
- **THEN** leaderboard reloads showing rankings for that historical date
- **AND** the clicked pill is highlighted with accent color

#### Scenario: Click "Latest" pill returns to real-time
- **WHEN** user clicks the "最新" (latest) pill
- **THEN** leaderboard reloads in real-time mode (no date parameter sent)
- **AND** all pills revert to default styling

#### Scenario: Timeline shows only trading days
- **WHEN** timeline renders
- **THEN** weekends and holidays are excluded from the date pill list

### Requirement: Modal forward validation section
When viewing a stock's detail modal on a historical date, the modal SHALL display a "Forward Validation" section below the battle parameter cards. This section MUST contain three metric cards (next-day, 3-day, 5-day returns) and a mini sparkline chart of the 10-day price trajectory after the selected date.

#### Scenario: Historical date modal shows forward returns
- **WHEN** user clicks a stock on a historical date leaderboard to open the modal
- **AND** the selected date is before the latest trading day
- **THEN** modal includes a forward validation section with return metrics and sparkline

#### Scenario: Latest date modal shows no forward returns
- **WHEN** user clicks a stock on the latest/real-time leaderboard
- **THEN** modal displays only the battle parameter cards (existing behavior)

#### Scenario: Forward returns color coding
- **WHEN** forward return value is positive
- **THEN** metric card shows green/positive indicator
- **AND** when negative, metric card shows red/negative indicator
