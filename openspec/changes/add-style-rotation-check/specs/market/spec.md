## MODIFIED Requirements

### Requirement: Market Diagnosis Indicators
The system SHALL provide a market-diagnosis endpoint that aggregates multiple market indicators into a composite market structure signal.

#### Scenario: Get market diagnosis
- **WHEN** GET /api/v1/market/market-diagnosis is called
- **THEN** response SHALL include 6 indicators: amplitude, consecutive, sector_rotation, limit_ratio, ma5_direction, and style_rotation

#### Scenario: Get market diagnosis with style rotation
- **WHEN** GET /api/v1/market/market-diagnosis is called
- **THEN** the `style_rotation` indicator SHALL contain:
  - `baskets`: dict of defense/tech/resource baskets with their SW industry codes
  - `price_5d`: 5-day price comparison with per-basket average returns and win/loss tracking
  - `flow_10d`: 10-day fund flow comparison with per-basket aggregated net inflow and win/loss tracking
  - `style_regime`: one of "OFFENSE" / "DEFENSE" / "RESOURCE_HEDGE" / "NEUTRAL"
  - `consecutive_days`: days the current regime has persisted
  - `suggestion`: actionable strategy guidance
  - `divergence_warning`: warning text if price-flow dimensions disagree for ≥ 5 days, or null
