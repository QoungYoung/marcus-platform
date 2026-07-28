## Purpose

Define per-index parameters for golden pit strategy: entry/exit thresholds, position sizing multipliers, resonance coefficients, and the global macro coefficient overlay for position sizing.
## Requirements
### Requirement: Per-index entry and exit thresholds
Each tracked broad-market index SHALL have its own configuration for entry percentile threshold, exit percentile thresholds, and turning point confirmation days, replacing global constants where applicable.

#### Scenario: Different entry thresholds per index
- **WHEN** the system evaluates golden pit status for 科创50 (high elasticity)
- **THEN** the entry warning threshold SHALL be P15 (more aggressive early entry)
- **WHEN** the system evaluates golden pit status for 沪深300 (low elasticity)
- **THEN** the entry warning threshold SHALL be P5 (more conservative, only enter deep pits)

#### Scenario: Different turning point confirmation days
- **WHEN** the system detects trend for 科创50 (fast recovery)
- **THEN** turning point confirmation SHALL require 1 consecutive rising day
- **WHEN** the system detects trend for 沪深300 (slow recovery)
- **THEN** turning point confirmation SHALL require 2 consecutive rising days

### Requirement: Per-index position sizing multipliers
Each index SHALL have configurable position sizing multipliers that scale the base POSITION_TIERS percentages according to the index's historical return characteristics.

#### Scenario: Higher position weight for strong-signal indices
- **WHEN** 科创50 triggers a position tier of "turning" (base 50%)
- **THEN** the actual position SHALL be 50% × 1.2 = 60% of max_total (strong signal multiplier)
- **WHEN** 沪深300 triggers a position tier of "turning" (base 50%)
- **THEN** the actual position SHALL be 50% × 0.8 = 40% of max_total (defensive multiplier)

#### Scenario: Pre-turn cap varies by index
- **WHEN** the system calculates pre-turn cumulative cap for 科创50
- **THEN** the cap SHALL be max_total × 20% (higher cap for high-conviction index)
- **WHEN** the system calculates pre-turn cumulative cap for 沪深300
- **THEN** the cap SHALL be max_total × 10% (lower cap for defensive index)

### Requirement: Multi-index resonance coefficient
The DCA execution service SHALL apply a resonance multiplier to order amounts based on how many indices are simultaneously in golden pit status, reflecting stronger signal confidence when multiple indices confirm.

#### Scenario: Strong resonance with 4+ indices
- **WHEN** 4 or more tradeable indices have status="golden_pit"
- **THEN** the resonance multiplier SHALL be 1.3
- **THEN** individual order amounts SHALL be multiplied by 1.3, capped at max_total_amount

#### Scenario: Weak resonance with single index
- **WHEN** only 1 tradeable index has status="golden_pit" or "warning"
- **THEN** the resonance multiplier SHALL be 0.6
- **THEN** individual order amounts SHALL be multiplied by 0.6

#### Scenario: No resonance effect in idle phase
- **WHEN** the golden pit window phase is "idle"
- **THEN** the resonance multiplier SHALL be 1.0 (no effect, DCA is already skipped)

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

