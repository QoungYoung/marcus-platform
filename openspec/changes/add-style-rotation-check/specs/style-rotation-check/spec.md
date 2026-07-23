## ADDED Requirements

### Requirement: Style Rotation Detection
The system SHALL detect the relative strength of defensive, tech, and resource style baskets and output a style regime signal as part of the market-diagnosis endpoint.

#### Scenario: Price dimension comparison
- **WHEN** market-diagnosis is executed
- **THEN** the system fetches SW L1 industry codes via `index_classify`, maps them to defense/tech/resource baskets by name keyword, fetches their daily index data for the last 5 trading days via `index_daily`, computes each basket's average daily return, and compares them to determine which basket outperforms each day

#### Scenario: Fund flow dimension comparison
- **WHEN** market-diagnosis is executed
- **THEN** the system fetches all concept sector flow data (top 300), aggregates the `main_net` and `pct_change` by defense/tech/resource keywords for the last 10 trading days, and compares daily aggregated basket flows to determine which basket attracts more capital each day

#### Scenario: Style regime output
- **WHEN** both price and fund flow dimensions agree on the same leading style basket for ≥ 3 consecutive days
- **THEN** the system outputs the corresponding style regime: OFFENSE (tech-led), DEFENSE (defense-led), or RESOURCE_HEDGE (resource-led)

#### Scenario: Neutral regime when no consensus
- **WHEN** no single style basket leads both dimensions for ≥ 3 consecutive days
- **THEN** the system outputs NEUTRAL regime

#### Scenario: Divergence warning
- **WHEN** the price dimension and fund flow dimension disagree on the leading style for ≥ 5 consecutive days
- **THEN** the system outputs a divergence warning: "⚠️ 价格与资金背离" with a suggestion to reduce positions

#### Scenario: Quick exit on reversal
- **WHEN** the current style regime is not NEUTRAL and the opposing style basket outperforms for 1 day in either dimension
- **THEN** the system reverts to NEUTRAL regime immediately without waiting for confirmation

### Requirement: Style Basket Classification
The system SHALL dynamically classify SW L1 industries into style baskets using keyword matching on industry names.

#### Scenario: Dynamic SW code retrieval
- **WHEN** `_compute_style_rotation()` is called for the first time or cache is expired
- **THEN** the system calls `pro.index_classify(level='L1', src='SW2021')`, filters by `is_pub='1'`, and maps each industry to defense/tech/resource/other based on name keyword matching

#### Scenario: Defense basket keywords
- **WHEN** classifying SW industries
- **THEN** industries with names containing "银行", "公用", "煤炭", "石油石化", "交通运输", or "食品饮料" SHALL be assigned to the defense basket

#### Scenario: Tech basket keywords
- **WHEN** classifying SW industries
- **THEN** industries with names containing "电子", "计算机", "传媒", "通信", "电力设备", or "机械设备" SHALL be assigned to the tech basket

#### Scenario: Resource basket keywords
- **WHEN** classifying SW industries
- **THEN** industries with names containing "有色金属", "基础化工", or "钢铁" SHALL be assigned to the resource basket

#### Scenario: Match priority and exclusivity
- **WHEN** an industry name matches keywords in multiple baskets
- **THEN** it SHALL be assigned to the first matching basket in priority order: tech > resource > defense

### Requirement: Style Signal Integration
The system SHALL expose the style regime to downstream consumers (trade_graph, indicator) through the market_diagnosis database table.

#### Scenario: Style data in indicators_json
- **WHEN** market-diagnosis completes and saves to the database
- **THEN** the `indicators_json` field SHALL include a `style_rotation` object with basket definitions, price 5-day comparison, flow 10-day comparison, style_regime, consecutive_days, and suggestion fields

#### Scenario: AI prompt injection
- **WHEN** `_read_market_regime()` is called by trade_graph
- **THEN** the style regime SHALL be read from `indicators_json` and injected into the AI trading prompt as a style direction instruction

#### Scenario: Position cap adjustment
- **WHEN** `_get_market_regime_for_calc()` is called by indicator.py
- **THEN** the style regime SHALL influence sector-level position caps: in OFFENSE mode tech sector cap increases (15%→20%) and defense cap decreases (15%→10%); in DEFENSE mode the reverse applies

#### Scenario: QQ push display
- **WHEN** morning_diagnosis.py formats the diagnosis message
- **THEN** the style rotation conclusion SHALL be displayed as a new section with the style label, consecutive days, and actionable suggestion

## MODIFIED Requirements

### Requirement: Market Diagnosis Indicators
The system SHALL provide a market-diagnosis endpoint that aggregates multiple market indicators into a composite market structure signal.

#### Scenario: Get market diagnosis with style rotation
- **WHEN** GET /api/v1/market/market-diagnosis is called
- **THEN** response SHALL include a `style_rotation` object in the `indicators` dict containing:
  - `baskets`: dict of defense/tech/resource baskets with their SW industry codes
  - `price_5d`: dict with `defense_avg_return`, `tech_avg_return`, `resource_avg_return`, `leader`, `defense_win_days`, `tech_win_days`, `resource_win_days`, `consecutive_leader`, `consecutive_days`
  - `flow_10d`: dict with `defense_total_flow`, `tech_total_flow`, `resource_total_flow`, `leader`, `defense_win_days`, `tech_win_days`, `resource_win_days`, `consecutive_leader`, `consecutive_days`
  - `style_regime`: string enum of "OFFENSE" / "DEFENSE" / "RESOURCE_HEDGE" / "NEUTRAL"
  - `consecutive_days`: int, days the current regime has persisted
  - `suggestion`: string, actionable strategy guidance based on style regime
  - `divergence_warning`: string or null, warning if price-flow dimensions disagree for ≥ 5 days
