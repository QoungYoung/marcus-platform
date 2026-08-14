## MODIFIED Requirements

### Requirement: get_concept_fund_flow_5d Tool Registration
The DSH container SHALL register a `get_concept_fund_flow_5d` tool accessible to the trading AI.

#### Scenario: Tool definition
- **WHEN** the tool is registered in the DSH native tool registry
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
