## ADDED Requirements

### Requirement: Capitulation volume detection
The system SHALL detect capitulation patterns using volume acceleration combined with price drawdown.

#### Scenario: Capitulation signal detected
- **WHEN** a stock's 5-day average volume exceeds 2.0x its 20-day average volume
- **AND** the 5-day cumulative return is below -5%
- **THEN** the capitulation volume sub-score SHALL be 4

#### Scenario: Mild capitulation
- **WHEN** 5-day average volume is 1.5-2.0x 20-day average AND 5-day return is below -3%
- **THEN** the capitulation volume sub-score SHALL be 2

#### Scenario: No capitulation
- **WHEN** volume acceleration is below 1.5x OR 5-day return is above 0%
- **THEN** the capitulation volume sub-score SHALL be 0

### Requirement: Mean-reversion distance scoring
The system SHALL measure the stock's distance below its 20-day moving average in standard deviation units.

#### Scenario: Deep oversold
- **WHEN** price is more than 2 standard deviations below MA20 (Bollinger Band lower penetration)
- **THEN** the mean-reversion sub-score SHALL be 4

#### Scenario: Moderate oversold
- **WHEN** price is 1-2 standard deviations below MA20
- **THEN** the mean-reversion sub-score SHALL be 2

#### Scenario: At or above MA20
- **WHEN** price is at or above MA20
- **THEN** the mean-reversion sub-score SHALL be 0

### Requirement: Quality filter for reversal eligibility
The system SHALL apply a quality filter before computing reversal_score to exclude falling knives.

#### Scenario: Stock passes quality filter
- **WHEN** a stock's market cap exceeds the industry median
- **AND** its 20-day cumulative return is above -20%
- **THEN** reversal_score SHALL be computed normally

#### Scenario: Stock fails quality filter
- **WHEN** a stock's market cap is below industry median OR 20-day return is below -20%
- **THEN** reversal_score SHALL be 0
- **AND** the reason SHALL indicate which filter was failed

### Requirement: Reversal score composite
The system SHALL compute reversal_score as the sum of capitulation volume, mean-reversion distance, and quality sub-scores, capped at 10.

#### Scenario: Reversal score in leaderboard response
- **WHEN** GET /api/v1/market/industry-leaderboard is called
- **THEN** each item SHALL include reversal_score with value in range [0, 10]
- **AND** score_detail SHALL include a reversal entry documenting sub-scores and the quality filter result
