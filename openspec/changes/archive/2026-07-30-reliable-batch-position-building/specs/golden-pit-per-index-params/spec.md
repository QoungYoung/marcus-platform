## MODIFIED Requirements

### Requirement: Per-index entry and exit thresholds
Each tracked broad-market index SHALL have its own configuration for entry percentile threshold, exit percentile thresholds, turning point confirmation days, DCA strategy, trend factor overrides, and DCA fallback days, replacing global constants where applicable.

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

#### Scenario: Different DCA strategies per index
- **WHEN** the system executes DCA for 科创50 (high win rate, strong trend)
- **THEN** `dca_strategy` SHALL be `lump_entry` (100% day 1)
- **WHEN** the system executes DCA for 中证1000 (high volatility, lower win rate)
- **THEN** `dca_strategy` SHALL be `uniform_3` (33% over 3 days)

## ADDED Requirements

### Requirement: Per-index trend factor overrides
Each index SHALL support an optional `trend_factors` dictionary that overrides the global default trend-to-factor mapping, allowing different indices to have different acceleration/deceleration profiles.

#### Scenario: Custom declining factor for volatile index
- **WHEN** 中证1000 has `trend_factors` = `{"declining": 0.15, "full": 1.3}`
- **WHEN** trend state is `declining`
- **THEN** trend factor SHALL be 0.15 (override) instead of global default 0.1

#### Scenario: Global default fallback for uncovered states
- **WHEN** 中证1000 has `trend_factors` = `{"declining": 0.15}` only
- **WHEN** trend state is `turning`
- **THEN** trend factor SHALL be 1.0 (global default, since turning is not overridden)

#### Scenario: No trend_factors defined
- **WHEN** an index has no `trend_factors` key in CHINA_INDICES
- **THEN** ALL trend states SHALL use global defaults

### Requirement: Per-index DCA fallback days
Each index SHALL have a `dca_fallback` field that defines the maximum number of calendar days the DCA schedule window can span. If the window exceeds this limit without completing the schedule (due to safety brakes or persistent declining trend), the system SHALL force-complete remaining planned buys.

#### Scenario: DCA fallback triggers force completion
- **WHEN** 恒生指数 has `dca_fallback` = 15 and schedule has been active for 16 days
- **WHEN** trend factor is still suppressed (declining state)
- **THEN** trend factor SHALL be forced to 1.0
- **THEN** remaining DCA schedule days SHALL execute at full weight

#### Scenario: Short fallback for lump_entry indices
- **WHEN** 科创50 has `dca_fallback` = 5 and `dca_strategy` = `lump_entry`
- **THEN** if lump_entry fails to execute on day 0 due to safety brake
- **THEN** the system SHALL attempt full execution by day 5
