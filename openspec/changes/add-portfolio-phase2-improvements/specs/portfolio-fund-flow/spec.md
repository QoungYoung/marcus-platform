## ADDED Requirements

(This spec covers both the per-position badge and aggregated summary strip.)

### Requirement: Per-Position Fund Flow Badge
The system SHALL display a moneyflow direction badge on each position row showing the main force net flow for that stock.

#### Scenario: Main force net inflow
- **WHEN** the stock's main force net flow (`main_net`) > 0 and its absolute value exceeds 1% of the position's market value
- **THEN** the badge displays "主力流入" in the agent green color

#### Scenario: Main force net outflow
- **WHEN** the stock's `main_net` < 0 and its absolute value exceeds 1% of the position's market value
- **THEN** the badge displays "主力流出" in the agent red color

#### Scenario: Balanced flow
- **WHEN** the stock's `main_net` magnitude is below 1% of the position's market value
- **THEN** the badge displays "平衡" in dim text color

#### Scenario: Moneyflow data unavailable
- **WHEN** the moneyflow API call fails or returns no data for a stock
- **THEN** the badge displays "—" in dim text color

### Requirement: Fund Flow Summary Strip
The system SHALL display an aggregated fund flow summary above the positions table header.

#### Scenario: Mixed flow across positions
- **WHEN** at least one position shows inflow and at least one shows outflow
- **THEN** the summary strip shows "X只流入 / Y只流出" with inflow count in green and outflow count in red

#### Scenario: All positions showing inflow
- **WHEN** all positions with successful moneyflow data show main_net > 0
- **THEN** the summary strip shows "主力全部流入" in green

#### Scenario: All positions showing outflow
- **WHEN** all positions with successful moneyflow data show main_net < 0
- **THEN** the summary strip shows "主力全部流出" in red

#### Scenario: No moneyflow data available
- **WHEN** all moneyflow API calls fail
- **THEN** the summary strip is hidden entirely
