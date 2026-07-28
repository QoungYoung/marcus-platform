## ADDED Requirements

### Requirement: Macro-driven early stop-profit
The system SHALL emit a stop-profit signal when global risk appetite reaches extreme greed territory and the position has recovered above cost basis, independent of A-share-specific greed percentile thresholds.

#### Scenario: Global extreme greed triggers half exit
- **WHEN** the global capital flow `sentiment_score` exceeds 80 AND the index has a confirmed turning point AND the position profit exceeds 5%
- **THEN** the system SHALL emit a "half_exit" signal with reason "全球风险偏好极端贪婪，建议减持50%"

#### Scenario: Global macro exit defers to stronger A-share exit
- **WHEN** global macro triggers "half_exit" BUT A-share greed percentile triggers "full_exit" (P50)
- **THEN** the system SHALL use "full_exit" as the effective signal (strongest wins)

#### Scenario: No global macro exit without profit
- **WHEN** sentiment_score exceeds 80 BUT position profit is ≤5%
- **THEN** the system SHALL NOT emit a global macro exit signal

## MODIFIED Requirements

### Requirement: Exit signal based on greed recovery percentile
The system SHALL detect exit signals for indices that are currently in golden pit or warning status. An exit signal is triggered when the greed value's expanding-window percentile rises above a configured threshold after the turning point has been confirmed. Additionally, the system SHALL consider global risk appetite levels as an independent exit trigger.

#### Scenario: Half exit at P30 after turning point
- **WHEN** an index has turning_point_confirmed=True AND its greed percentile rises above P30 for the first time since turning point
- **THEN** the system SHALL emit a "half_exit" signal, indicating 50% of the position should be sold

#### Scenario: Full exit at P50 after turning point
- **WHEN** an index has turning_point_confirmed=True AND its greed percentile rises above P50
- **THEN** the system SHALL emit a "full_exit" signal, indicating the entire position should be sold

#### Scenario: Stop-profit on trend reversal
- **WHEN** an index has turning_point_confirmed=True AND greed has declined for 2+ consecutive days after recovering above P30
- **THEN** the system SHALL emit a "stop_profit" signal, indicating the position should be sold to protect gains

#### Scenario: Global extreme greed triggers half exit
- **WHEN** global sentiment_score > 80 AND turning_point_confirmed=True AND position profit > 5%
- **THEN** the system SHALL emit a "half_exit" signal with reason referencing global risk appetite

#### Scenario: Strongest exit signal wins
- **WHEN** multiple exit sources produce different signal levels (e.g., macro=half_exit, A-share=full_exit)
- **THEN** the system SHALL resolve to the most aggressive signal ("full_exit" > "half_exit" > "stop_profit")

#### Scenario: No exit signal before turning point
- **WHEN** an index has turning_point_confirmed=False
- **THEN** the system SHALL NOT emit any exit signal, regardless of greed percentile or global sentiment
