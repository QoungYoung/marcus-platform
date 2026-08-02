## ADDED Requirements

### Requirement: 5-Day Concept Fund Flow Aggregation API
The system SHALL provide an endpoint `GET /api/v1/market/concept-fund-flow-5d` that aggregates concept sector fund flow data over the last N trading days and returns ranked results with composite scores.

#### Scenario: Default 5-day aggregation
- **WHEN** the API is called with default parameter `days=5`
- **THEN** the system queries the last 5 trading days from Tushare `moneyflow_ind_dc` with `content_type=概念`
- **AND** groups records by concept name
- **AND** for each concept calculates: total_pct_change (SUM), up_days (COUNT of days where pct_change > 0), total_net_amount (SUM)
- **AND** returns results sorted by composite score descending

#### Scenario: Custom day count
- **WHEN** the API is called with `days=10`
- **THEN** the system queries the last 10 trading days instead of 5

#### Scenario: Trading day filtering
- **WHEN** the API calculates the date range
- **THEN** the system SHALL use Tushare `trade_cal` to determine the actual trading days
- **AND** SHALL exclude weekends and holidays from the N-day window

### Requirement: Dark Track Scoring Model
The system SHALL compute a composite score for each concept based on cumulative performance ranking and sustainability ranking.

#### Scenario: Composite score calculation
- **WHEN** all concepts are aggregated
- **THEN** the system SHALL rank concepts by 5-day cumulative pct_change descending and assign rank scores (1st=10, 2nd=9, ..., 10th=1, 11th+=0)
- **AND** SHALL rank concepts by up_days descending and assign rank scores (1st=10, 2nd=9, ..., 10th=1, 11th+=0)
- **AND** SHALL compute final score as `pct_rank_score * 0.5 + up_days_rank_score * 0.5`
- **AND** full score range is 0.0 to 10.0

#### Scenario: Ties in ranking
- **WHEN** multiple concepts have the same cumulative pct_change or up_days
- **THEN** the system SHALL assign the same rank score to tied concepts
- **AND** SHALL skip subsequent ranks accordingly (e.g., two tied at 1st → both get 10, next gets 8)

### Requirement: Daily Fund Flow Gatekeeper
The system SHALL filter out concepts whose today's main net inflow is non-positive before scoring and ranking.

#### Scenario: Gatekeeper passes
- **WHEN** a concept has today's net_amount > 0
- **THEN** the concept SHALL be included in the dark track ranking

#### Scenario: Gatekeeper blocks
- **WHEN** a concept has today's net_amount <= 0
- **THEN** the concept SHALL be excluded from the dark track ranking entirely
- **AND** SHALL NOT appear in the API response

#### Scenario: Today data unavailable
- **WHEN** today's trade date data is not yet available from Tushare
- **THEN** the system SHALL use the most recent trading day's data as the gatekeeper check
- **AND** SHALL include a field `data_date` in the response indicating the latest data date used

### Requirement: API Response Format
The system SHALL return dark track results in a structured JSON format compatible with the existing concept-fund-flow response pattern.

#### Scenario: Successful response
- **WHEN** the API call succeeds
- **THEN** the response SHALL include:
  - `data_date`: the latest trading date with data
  - `trading_days`: list of trading dates included in the aggregation
  - `items`: array of concept objects, each containing `name`, `total_pct_change`, `up_days`, `total_net_amount`, `pct_rank_score`, `up_days_rank_score`, `composite_score`, `today_net_amount`

#### Scenario: Error handling
- **WHEN** Tushare API is unavailable or returns an error
- **THEN** the system SHALL return HTTP 502 with a clear error message
- **AND** SHALL log the underlying error for debugging
