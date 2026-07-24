## ADDED Requirements

### Requirement: CCI overbought filtering
The system SHALL check the CCI (Commodity Channel Index) value as part of Layer 3 overbought filtering and grade it according to the following thresholds:

| CCI Range | Grade | Action |
|-----------|-------|--------|
| < 150 | Normal (✅) | Pass |
| 150 - 200 | Warning (⚠️) | Probe position only, multiplier ≤ 0.5 |
| 200 - 300 | Blocked (🚫) | No entry, multiplier = 0 |
| ≥ 300 | Hard Block (🔴) | No entry, cannot be exempted by sector signal |

#### Scenario: CCI normal
- **WHEN** CCI is 120 and RSI6 is 60 and KDJ-J is 80
- **THEN** CCI is graded "✅ Normal" and contributes severity 0

#### Scenario: CCI probe only
- **WHEN** CCI is 175
- **THEN** CCI is graded "⚠️ Only Probe" and triggers downgrade_multiplier ≤ 0.5

#### Scenario: CCI blocked
- **WHEN** CCI is 250
- **THEN** CCI is graded "🚫 Blocked" and sets downgrade_multiplier to 0

#### Scenario: CCI hard blocked
- **WHEN** CCI is 320
- **THEN** CCI is graded "🔴 Hard Block" and entry is forbidden regardless of sector signal

### Requirement: Tightened RSI6 thresholds
The system SHALL use tightened RSI6 thresholds for Layer 3 overbought filtering:

| RSI6 Range | Grade | Action |
|------------|-------|--------|
| < 75 | Normal (✅) | Pass |
| 75 - 85 | Warning (⚠️) | Probe position only, multiplier ≤ 0.5 |
| 85 - 95 | Blocked (🚫) | No entry, multiplier = 0 |
| ≥ 95 | Hard Block (🔴) | No entry, cannot be exempted by sector signal |

#### Scenario: RSI6 normal under new threshold
- **WHEN** RSI6 is 70 and CCI is 120 and KDJ-J is 80
- **THEN** RSI6 is graded "✅ Normal"

#### Scenario: RSI6 probe only under new threshold
- **WHEN** RSI6 is 80
- **THEN** RSI6 is graded "⚠️ Only Probe" and triggers downgrade_multiplier ≤ 0.5

#### Scenario: RSI6 blocked under new threshold
- **WHEN** RSI6 is 88
- **THEN** RSI6 is graded "🚫 Blocked" and sets downgrade_multiplier to 0

### Requirement: Tightened KDJ-J thresholds
The system SHALL use tightened KDJ-J thresholds for Layer 3 overbought filtering:

| KDJ-J Range | Grade | Action |
|-------------|-------|--------|
| < 95 | Normal (✅) | Pass |
| 95 - 105 | Warning (⚠️) | Probe position only, multiplier ≤ 0.5 |
| 105 - 120 | Blocked (🚫) | No entry, multiplier = 0 |
| ≥ 120 | Hard Block (🔴) | No entry, cannot be exempted by sector signal |

#### Scenario: KDJ-J normal under new threshold
- **WHEN** KDJ-J is 90 and CCI is 120 and RSI6 is 60
- **THEN** KDJ-J is graded "✅ Normal"

#### Scenario: KDJ-J probe only under new threshold
- **WHEN** KDJ-J is 100
- **THEN** KDJ-J is graded "⚠️ Only Probe" and triggers downgrade_multiplier ≤ 0.5

#### Scenario: KDJ-J blocked under new threshold
- **WHEN** KDJ-J is 108
- **THEN** KDJ-J is graded "🚫 Blocked" and sets downgrade_multiplier to 0

### Requirement: Multi-indicator resonance detection
The system SHALL compute a per-indicator severity level (0=Normal, 1=Probe, 2=Blocked, 3=HardBlock) and apply resonance downgrade when two or more indicators are at severity ≥ 1.

The final grade SHALL be determined as follows:
1. Find the highest severity across RSI6, KDJ-J, and CCI
2. If ≥ 2 indicators have severity ≥ 1, SHIFT the highest severity up by one level (max 3)
3. The shifted severity maps to the final grade:
   - 0 → "✅通过" (Pass)
   - 1 → "⚠️仅试探仓" (Probe only)
   - 2 → "🚫排除" (Blocked)
   - 3 → "🔴硬禁止" (Hard Block)

Hard block (severity 3) from any single indicator SHALL always block entry regardless of resonance logic.

#### Scenario: Two indicators warn, resonance downgrades to blocked
- **WHEN** RSI6 is 78 (severity 1) and KDJ-J is 98 (severity 1) and CCI is 120 (severity 0)
- **THEN** highest severity is 1, but 2 indicators ≥ severity 1 triggers resonance
- **AND** final severity becomes 2 (🚫 Blocked)

#### Scenario: Single indicator warns, no resonance
- **WHEN** RSI6 is 78 (severity 1) and KDJ-J is 80 (severity 0) and CCI is 120 (severity 0)
- **THEN** highest severity is 1 with no resonance trigger
- **AND** final severity stays 1 (⚠️ Only Probe)

#### Scenario: Hard block always blocks regardless of resonance
- **WHEN** RSI6 is 96 (severity 3, hard block) and KDJ-J is 80 (severity 0) and CCI is 120 (severity 0)
- **THEN** entry is hard blocked regardless of other indicators

#### Scenario: 紫金矿业 case would be caught
- **WHEN** RSI6 is 83.9 (severity 1, warning under new threshold) and KDJ-J is 102.8 (severity 1, warning under new threshold) and CCI is 250.2 (severity 2, blocked under new threshold)
- **THEN** at least 2 indicators at severity ≥ 1, resonance triggers
- **AND** final severity is at least 3 (🔴 Hard Block or 🚫 Blocked depending on CCI hard block threshold)
- **AND** entry is denied

### Requirement: Backward compatible API response
The system SHALL maintain the existing `LayerResult` response schema (fields: `passed`, `grade`, `details`, `downgrade_reason`, `downgrade_action`) without adding or removing fields.

#### Scenario: Response structure unchanged
- **WHEN** a client calls POST /api/v1/indicator/check-entry-filters
- **THEN** the `layer3_overbought` field contains the same keys as before the change
