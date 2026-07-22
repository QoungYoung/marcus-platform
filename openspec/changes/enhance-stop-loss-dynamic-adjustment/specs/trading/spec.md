## ADDED Requirements

### Requirement: High-volatility cost stop threshold widening (all tiers)
The system SHALL dynamically widen the cost stop loss threshold for all three position tiers using the formula `max(base_threshold, amplitude × coefficient)`, where base_threshold and coefficient vary by scenario.

#### Scenario: Low volatility never-profitable uses standard threshold
- **WHEN** a position has never been profitable and the stock's 5-day average amplitude is 3%
- **THEN** the stop loss threshold SHALL be max(-4%, 3% × 0.40) = -4%

#### Scenario: High volatility never-profitable widens threshold
- **WHEN** a position has never been profitable and the stock's 5-day average amplitude is 12%
- **THEN** the stop loss threshold SHALL be max(-4%, 12% × 0.40) = -4.8%

#### Scenario: High volatility small-profit retracement widens in parallel
- **WHEN** a position had small profit (3-5%) and the stock's 5-day average amplitude is 12%
- **THEN** the stop loss threshold SHALL be max(-3%, 12% × 0.40) = -4.8%

#### Scenario: Low volatility small-profit retracement keeps existing threshold
- **WHEN** a position had small profit (3-5%) and the stock's 5-day average amplitude is 4%
- **THEN** the stop loss threshold SHALL be max(-3%, 4% × 0.40) = -3%

#### Scenario: No HWM with high volatility uses higher coefficient
- **WHEN** a position has no HWM data and the stock's 5-day average amplitude is 12%
- **THEN** the stop loss threshold SHALL be max(-6%, 12% × 0.50) = -6%

### Requirement: 60-minute trend validation before stop loss execution
The system SHALL validate the 60-minute K-line trend direction before executing any stop loss order. If the MA10 moving average is above the MA30 moving average on 60-minute candles, the system SHALL suspend the stop loss execution and mark the position with a "恐慌错杀警戒" (panic-selling false alarm) warning.

#### Scenario: Uptrend intact — stop loss suspended
- **WHEN** a stop loss rule triggers for a position
- **AND** the system fetches 60-minute intraday K-line data
- **AND** MA10 > MA30 on the 60-minute candles
- **THEN** the stop loss execution SHALL be suspended
- **AND** the position SHALL be marked as "恐慌错杀警戒"
- **AND** the suspension reason SHALL be logged

#### Scenario: Downtrend confirmed — stop loss proceeds
- **WHEN** a stop loss rule triggers for a position
- **AND** the system fetches 60-minute intraday K-line data
- **AND** MA10 ≤ MA30 on the 60-minute candles
- **THEN** the stop loss execution SHALL proceed normally

#### Scenario: 60-minute data unavailable — stop loss proceeds
- **WHEN** a stop loss rule triggers for a position
- **AND** the 60-minute intraday data fetch fails or times out (3 seconds)
- **THEN** the stop loss execution SHALL proceed normally without trend validation

#### Scenario: Repeated suspension cap
- **WHEN** a position has been suspended by the 60-minute trend validation 3 times in the same trading day
- **AND** a stop loss rule triggers again for the same position
- **THEN** the 60-minute trend validation SHALL be skipped
- **AND** the stop loss execution SHALL proceed directly

### Requirement: Rule 1 sector divergence threshold adaptation in extreme panic
The system SHALL dynamically widen the sector divergence stop loss threshold (rule 1) when the market-wide main force net outflow exceeds 300 billion CNY, using a two-step decision tree to distinguish genuine weakness from liquidity-driven mispricing.

#### Scenario: Normal market — rule 1 uses standard threshold
- **WHEN** the market-wide main force net outflow is ≤ 300 billion CNY
- **THEN** rule 1 SHALL use the standard -3pp threshold regardless of sector or individual stock flow

#### Scenario: Extreme panic with individual stock distribution — no adjustment
- **WHEN** market-wide main force net outflow > 300 billion CNY
- **AND** the individual stock's main force net outflow > 0.3 billion CNY (or net ratio < -10%)
- **THEN** rule 1 SHALL keep the standard -3pp threshold
- **AND** the stop loss SHALL proceed as normal (genuine distribution detected)

#### Scenario: Extreme panic with strong sector buying — maximum widening
- **WHEN** market-wide main force net outflow > 300 billion CNY
- **AND** the individual stock's main force net outflow ≤ 0.3 billion CNY
- **AND** the sector's main force net inflow > 10 billion CNY
- **THEN** rule 1 threshold SHALL widen from -3pp to -8pp

#### Scenario: Extreme panic with moderate sector buying — moderate widening
- **WHEN** market-wide main force net outflow > 300 billion CNY
- **AND** the individual stock's main force net outflow ≤ 0.3 billion CNY
- **AND** the sector's main force net inflow is between 0 and 10 billion CNY
- **THEN** rule 1 threshold SHALL widen from -3pp to -5pp

#### Scenario: Extreme panic with sector distribution — no widening
- **WHEN** market-wide main force net outflow > 300 billion CNY
- **AND** the individual stock's main force net outflow ≤ 0.3 billion CNY
- **AND** the sector's main force is also in net outflow
- **THEN** rule 1 SHALL keep the standard -3pp threshold

#### Scenario: Stock flow data unavailable — skip individual defense
- **WHEN** market-wide main force net outflow > 300 billion CNY
- **AND** the individual stock moneyflow data is unavailable or times out (2 seconds)
- **THEN** the system SHALL proceed to STEP 2 (sector assessment) without the individual stock defense check
