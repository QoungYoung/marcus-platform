## MODIFIED Requirements

### Requirement: Multi-indicator resonance detection
The system SHALL compute a per-indicator severity level for RSI6, KDJ-J, CCI, K-line patterns, and volume-price divergence, then apply resonance downgrade when two or more indicators are at severity ≥ 1.

The final grade SHALL be determined as follows:
1. Compute overbought severity for RSI6, KDJ-J, CCI (existing thresholds)
2. Compute pattern severity: 0 if no pattern detected, 3 (Hard Block) if shooting star or bearish engulfing detected
3. Compute divergence severity: 0 if no divergence, 1 (Warning) if volume-price divergence detected
4. Find the highest severity across all five indicators
5. If ≥ 2 indicators have severity ≥ 1, SHIFT the highest severity up by one level (max 3)
6. If pattern severity is 3 (Hard Block), final severity SHALL be 3 regardless of resonance logic
7. The shifted severity maps to the final grade: 0 → Pass, 1 → Probe only, 2 → Blocked, 3 → Hard Block

#### Scenario: Shooting star hard blocks regardless of other indicators
- **WHEN** RSI6 is 65 (severity 0) and KDJ-J is 80 (severity 0) and CCI is 100 (severity 0)
- **AND** shooting star pattern detected (severity 3)
- **THEN** entry is hard blocked by pattern regardless of clean overbought indicators

#### Scenario: BYD July 21 full case
- **WHEN** RSI6 is 72.03 (severity 0), KDJ-J is 80.68 (severity 0), CCI is 123.61 (severity 0)
- **AND** shooting star pattern detected (severity 3)
- **AND** volume-price divergence detected (severity 1)
- **THEN** at least 2 indicators at severity ≥ 1
- **AND** final severity is 3 (Hard Block)
- **AND** entry is denied
