## MODIFIED Requirements

### Requirement: Industry leaderboard API endpoint
The system SHALL expose a REST endpoint that returns industry leaderboard rankings with redesigned dimension scores.

#### Scenario: Default ranking with new fields
- **WHEN** GET /api/v1/market/industry-leaderboard is called without parameters
- **THEN** each item SHALL include valuation_score, reversal_score, and risk_score in addition to existing fields
- **AND** the response SHALL include a score_families metadata field documenting the 4-family taxonomy used

#### Scenario: Deprecated fields during migration
- **WHEN** GET /api/v1/market/industry-leaderboard is called
- **THEN** each item SHALL still include overbought_score (deprecated, set to 0) for backward compatibility
- **AND** each item SHALL still include price_residual_score with its legacy computation
- **AND** a new field price_residual_v2 SHALL contain the redesigned residual score

#### Scenario: Sort by new dimension
- **WHEN** GET /api/v1/market/industry-leaderboard?sort_by=valuation_score is called
- **THEN** items SHALL be sorted by valuation_score descending

## ADDED Requirements

### Requirement: Score families metadata in response
The system SHALL include a score_families metadata object in the leaderboard response describing the taxonomy.

#### Scenario: Metadata includes family definitions
- **WHEN** the leaderboard response is generated
- **THEN** score_families SHALL list each family with its name, constituent dimensions, and current weight
- **AND** the market_regime SHALL determine the active weight set
