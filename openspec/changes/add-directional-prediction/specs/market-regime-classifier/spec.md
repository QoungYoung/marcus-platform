## ADDED Requirements

### Requirement: Daily market regime classification
The system SHALL classify each trading day as "favorable" or "unfavorable" for directional stock trading, based on market breadth, index trend, and volatility features computed from the candidate stock cross-section and index data.

#### Scenario: Favorable market day
- **WHEN** market breadth (up_ratio) exceeds the 20-day median AND index 5-day return is above -1%
- **THEN** the day is classified as "favorable" and stock-level predictions are generated with full confidence

#### Scenario: Unfavorable market day
- **WHEN** market breadth falls below the 20-day median AND index 5-day return is negative
- **THEN** the day is classified as "unfavorable" and stock-level predictions carry a confidence penalty

### Requirement: Regime features
The regime classifier SHALL use the following features computed from the daily candidate cross-section: up_ratio (fraction of stocks with positive daily return), limit_up_count (number of stocks at daily limit-up), advance_decline_ratio, market_mean_return, market_return_volatility, and index 5-day/20-day returns.

#### Scenario: Feature computation
- **WHEN** daily quotes are available for all candidate stocks
- **THEN** the system computes all regime features within a single cross-section pass, without additional API calls

### Requirement: Soft gate behavior
The regime classifier SHALL act as a soft gate, not a hard block. Predictions are generated for all days, but "unfavorable" classifications reduce the output confidence score by a configurable penalty factor.

#### Scenario: Unfavorable day prediction
- **WHEN** a day is classified as unfavorable
- **THEN** stock-level predictions are still generated but confidence scores are multiplied by the penalty factor
