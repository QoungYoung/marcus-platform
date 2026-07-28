# golden-pit-global-macro Specification

## Purpose
TBD - created by archiving change enhance-golden-pit-global-macro. Update Purpose after archive.
## Requirements
### Requirement: Liquidity gate hard stop on buying
The system SHALL check global macro liquidity conditions before executing any DCA buy orders. When the global risk appetite sentiment_score is in extreme fear territory (≤20), all buy orders SHALL be skipped for the current execution cycle.

#### Scenario: Gate closed blocks all buying
- **WHEN** the global capital flow `sentiment_score` is ≤20 (extreme fear)
- **THEN** the DCA execution SHALL skip all buy orders for all indices
- **THEN** the DCA log SHALL record each skipped order with status "skipped" and reason "global_liquidity_gate_closed"

#### Scenario: Gate open allows normal buying
- **WHEN** the global capital flow `sentiment_score` is >20
- **THEN** the DCA execution SHALL proceed with normal buy order evaluation

#### Scenario: Gate status visible in API response
- **WHEN** a client requests GET /golden-pit/status
- **THEN** the response SHALL contain a `global_macro` object with `liquidity_gate` field ("open" or "closed") and `sentiment_score`

### Requirement: Global risk trend cross-validates turning point
The system SHALL compute the global risk appetite trend from available time-series data in the global-capital-flow response, and use it to validate or question A-share index turning point confirmations.

#### Scenario: Global trend confirms A-share turning point
- **WHEN** an A-share index shows turning_point_confirmed=True AND the global risk trend is also rising (2+ consecutive days of improvement)
- **THEN** the turning point SHALL be treated as "validated" with normal position tier progression

#### Scenario: Global trend diverges from A-share turning point
- **WHEN** an A-share index shows turning_point_confirmed=True BUT the global risk trend is declining or flat
- **THEN** the position tier SHALL be capped at "pre_turn" (3% per day) regardless of days_rising count
- **THEN** the index SHALL have a `turning_validation` field set to "divergent" with explanation

#### Scenario: Global trend unavailable
- **WHEN** the global-capital-flow response does not contain usable time-series data
- **THEN** the system SHALL fall back to existing A-share-only turning point logic without error

### Requirement: Global macro coefficient adjusts position sizing
The system SHALL apply a `global_macro_coefficient` multiplier to DCA order amounts based on the global risk appetite level, after the existing resonance multiplier.

#### Scenario: Fear reduces position size
- **WHEN** sentiment_score is between 21 and 35 (fear)
- **THEN** the global_macro_coefficient SHALL be 0.5
- **THEN** order amounts SHALL be `max_total × tier_pct × pos_mult × resonance × 0.5`

#### Scenario: Extreme fear sets coefficient to zero
- **WHEN** sentiment_score is ≤20 (extreme fear)
- **THEN** the global_macro_coefficient SHALL be 0
- **THEN** all buy orders SHALL evaluate to zero (enforced by liquidity gate)

#### Scenario: Neutral-to-greedy uses neutral coefficient
- **WHEN** sentiment_score is between 36 and 75
- **THEN** the global_macro_coefficient SHALL be 1.0 (no adjustment)

#### Scenario: Extreme greed reduces exposure
- **WHEN** sentiment_score is >75 (extreme greed)
- **THEN** the global_macro_coefficient SHALL be 0.8
- **THEN** order amounts SHALL be reduced by 20%

#### Scenario: Coefficient does not affect upper bound
- **WHEN** global_macro_coefficient amplifies an order amount
- **THEN** the resulting amount SHALL still be capped at `max_total_amount - total_already_invested`

### Requirement: Global macro overlay available in status API
The golden pit status endpoint SHALL expose the parsed global macro overlay data for downstream consumers (DCA service, frontend, QQ reports).

#### Scenario: Global macro data in status response
- **WHEN** a client requests GET /golden-pit/status
- **THEN** the response SHALL contain a `global_macro` object with fields:
  - `liquidity_gate`: "open" or "closed"
  - `sentiment_score`: number (0-100)
  - `sentiment_label`: string
  - `global_trend`: "rising" | "declining" | "flat" | "unknown"
  - `global_macro_coefficient`: number (0-1.5)
  - `summary`: human-readable interpretation string

