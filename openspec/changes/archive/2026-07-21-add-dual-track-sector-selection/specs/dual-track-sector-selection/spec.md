## ADDED Requirements

### Requirement: Dual-Track Main Line Identification
The trading AI SHALL identify main sector lines using both bright track (single-day momentum) and dark track (5-day sustainability) with equal weight.

#### Scenario: Bright track identification
- **WHEN** the AI executes main line selection
- **THEN** it SHALL call `get_concept_fund_flow(limit=30, sort_by=main_net)` to get the single-day main net inflow TOP30
- **AND** SHALL call `get_concept_fund_flow(limit=30, sort_by=pct_change)` to get the single-day pct_change TOP30
- **AND** SHALL compute the intersection of TOP5 from each list as the bright track sectors

#### Scenario: Dark track identification
- **WHEN** the AI executes main line selection
- **THEN** it SHALL call `get_concept_fund_flow_5d(days=5, limit=30)` to get the dark track composite scores
- **AND** SHALL treat sectors with composite_score >= 7 as dark track candidates

#### Scenario: Combined main line output
- **WHEN** both tracks are computed
- **THEN** the AI SHALL output a structured main line summary listing:
  - Bright track sectors (with main_net rank and pct_change rank)
  - Dark track sectors (with composite_score, pct_rank_score, up_days_rank_score)
  - Dual-resonance sectors (present in both tracks)

### Requirement: Three-Tier Stock Selection Priority
The trading AI SHALL use a three-tier priority system for position sizing based on which track(s) a sector belongs to.

#### Scenario: Tier 1 - Dual resonance
- **WHEN** a sector is in both bright track TOP5 AND has dark track composite_score >= 7
- **THEN** stocks from this sector SHALL be allocated 10-15% position weight

#### Scenario: Tier 2 - Dark track high score
- **WHEN** a sector has dark track composite_score >= 7 but is NOT in bright track TOP5
- **THEN** the AI SHALL recognize it as a sustained slow-climbing sector (e.g., power utilities, dividend stocks)
- **AND** stocks from this sector SHALL be allocated 8-12% position weight

#### Scenario: Tier 3 - Bright track only
- **WHEN** a sector is in bright track TOP5 but has dark track composite_score < 7
- **THEN** the AI SHALL flag it as a potential one-day spike risk
- **AND** stocks from this sector SHALL be allocated at most 3% position weight
- **AND** the AI SHALL recommend tighter stop-loss for these positions

### Requirement: get_concept_fund_flow_5d Tool Registration
The pi-server SHALL register a `get_concept_fund_flow_5d` tool accessible to the trading AI.

#### Scenario: Tool definition
- **WHEN** the tool is registered in tools.ts
- **THEN** it SHALL have:
  - name: `get_concept_fund_flow_5d`
  - description: explaining the 5-day aggregation, scoring model, and gatekeeper logic
  - parameters: `days` (default 5, range 3-20), `limit` (default 30)
  - endpoint: `GET /api/v1/market/concept-fund-flow-5d`

#### Scenario: Tool output format
- **WHEN** the AI invokes the tool
- **THEN** the response SHALL be formatted as a readable table showing:
  - Rank, sector name, composite score, 5-day cumulative pct_change, up_days/5, today net amount
  - Sectors with composite_score >= 7 marked as dark track candidates

#### Scenario: Tool listed in CHAT_SYSTEM_PROMPT
- **WHEN** the CHAT_SYSTEM_PROMPT lists available tools
- **THEN** `get_concept_fund_flow_5d` SHALL appear alongside `get_concept_fund_flow` with its purpose and usage described
