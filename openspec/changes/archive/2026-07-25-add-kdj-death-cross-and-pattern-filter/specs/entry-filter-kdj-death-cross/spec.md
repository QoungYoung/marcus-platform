# Entry Filter KDJ Death Cross

## Purpose

Defines the KDJ high-level death cross detection added to Layer 1 (technical filter) of `check_entry_filters`. Monitors the KDJ-K and KDJ-D lines to detect bearish crossover at elevated levels, providing an early momentum-exhaustion warning that precedes the MACD death cross.

## ADDED Requirements

### Requirement: KDJ high-level death cross detection
The system SHALL compare the current day's KDJ-K and KDJ-D values with the previous trading day's confirmed values to detect a death cross (K crossing below D).

The system SHALL classify the death cross severity as follows:

| Condition | Grade | Action |
|-----------|-------|--------|
| K > D (no cross) | Normal | Pass |
| K < D and K < 70 | Low-level cross | Pass (not significant) |
| K < D and 70 ≤ K < 80 | Mid-level cross | Warning: 仅试探仓, multiplier ≤ 0.5 |
| K < D and K ≥ 80 | High-level cross | Blocked: 禁止入场, multiplier = 0 |

#### Scenario: No death cross
- **WHEN** KDJ-K is 82.5 and KDJ-D is 81.0 (K > D)
- **THEN** KDJ death cross check passes with grade "Normal"

#### Scenario: Low-level death cross passes
- **WHEN** KDJ-K is 65.0 and KDJ-D is 68.0 (K < D, K < 70)
- **THEN** KDJ death cross check passes with grade "Normal" (low-level cross not significant)

#### Scenario: Mid-level death cross warns
- **WHEN** KDJ-K is 74.0 and KDJ-D is 77.0 (K < D, 70 ≤ K < 80)
- **THEN** KDJ death cross triggers "Warning" and sets downgrade_multiplier ≤ 0.5

#### Scenario: High-level death cross blocks entry
- **WHEN** KDJ-K is 81.6 and KDJ-D is 82.1 (K < D, K ≥ 80)
- **THEN** KDJ death cross triggers "Blocked" and sets downgrade_multiplier to 0

#### Scenario: BYD July 21 case would be caught
- **WHEN** KDJ-K is 81.60 and KDJ-D is 82.06 (K < D, K ≥ 80) on 2026-07-21
- **THEN** entry is blocked by KDJ high-level death cross regardless of MACD golden cross status

### Requirement: Data source for KDJ death cross
The system SHALL use the previous trading day's confirmed KDJ-K and KDJ-D values from Tushare stk_factor_pro (accessed via `get_realtime_indicators`'s `historical` field) as the reference point, and compare against current day's estimated KDJ-K/KDJ-D from `realtime` to detect the crossover.

#### Scenario: Uses confirmed historical data
- **WHEN** checking KDJ death cross for a stock
- **THEN** the previous day's KDJ-K and KDJ-D come from `historical[0]` of `get_realtime_indicators` response
- **AND** the current day's KDJ-K and KDJ-D come from the `realtime` field
