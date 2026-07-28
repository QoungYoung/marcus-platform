## ADDED Requirements

### Requirement: Global macro coefficient in position sizing
Each index's position sizing calculation SHALL incorporate a `global_macro_coefficient` multiplier derived from the global capital flow sentiment score, applied after the existing resonance multiplier. This coefficient SHALL be computed once per DCA execution cycle and applied uniformly to all indices.

#### Scenario: Global coefficient applies after resonance
- **WHEN** the DCA service calculates an order amount for any index
- **THEN** the effective amount SHALL be `max_total × tier_pct × position_multiplier × resonance × global_macro_coefficient`
- **THEN** the coefficient SHALL NOT cause the cumulative investment to exceed `max_total_amount`

#### Scenario: Coefficient range validation
- **WHEN** the global_macro_coefficient is computed
- **THEN** its value SHALL be in the range [0, 1.5]
- **THEN** values outside this range SHALL be clamped
