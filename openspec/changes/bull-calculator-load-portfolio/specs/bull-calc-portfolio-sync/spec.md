## ADDED Requirements

### Requirement: Load positions from server API
The system SHALL provide a button in the bull stock calculator that fetches the current portfolio from `GET /api/v1/portfolio/positions` and populates the calculator table.

#### Scenario: Load positions into empty calculator
- **WHEN** the user clicks "加载持仓" with an empty calculator list
- **THEN** all server positions are added as calculator rows with `high_water_mark` → `high`, `avg_price` → `cost`, `current_price` → `now`

#### Scenario: Load positions with existing entries
- **WHEN** the user clicks "加载持仓" and some stocks already exist in the calculator
- **THEN** existing stocks SHALL only have their price refreshed
- **AND** new stocks from the server SHALL be appended

#### Scenario: API unavailable
- **WHEN** the server API returns an error or is unreachable
- **THEN** the calculator SHALL display an error message and NOT clear existing data

### Requirement: Field mapping
The system SHALL map server `PositionResponse` fields to calculator fields as follows: `high_water_mark` → `high` (fallback to `current_price` if null), `avg_price` → `cost`, `current_price` → `now`. The `low` field SHALL be left unset.

#### Scenario: Position with HWM
- **WHEN** a position has `high_water_mark` set
- **THEN** the calculator `high` field SHALL be populated with that value

#### Scenario: Position without HWM
- **WHEN** a position has `high_water_mark` = null
- **THEN** the calculator `high` field SHALL fallback to `current_price`
