## ADDED Requirements

### Requirement: PE percentile scoring within industry
The system SHALL compute a valuation sub-score based on PE(TTM) percentile rank within the stock's industry, where lower PE earns higher score.

#### Scenario: PE in bottom quartile of industry
- **WHEN** a stock's PE(TTM) is in the bottom 25% of its industry peers
- **THEN** the PE sub-score SHALL be 5 (maximum)

#### Scenario: PE in top quartile of industry
- **WHEN** a stock's PE(TTM) is in the top 25% of its industry peers
- **THEN** the PE sub-score SHALL be 0-1

#### Scenario: PE data missing
- **WHEN** PE(TTM) is zero, NaN, or not available for a stock
- **THEN** the PE sub-score SHALL default to the industry median score (2.5)

### Requirement: PB percentile scoring within industry
The system SHALL compute a valuation sub-score based on PB percentile rank within the stock's industry.

#### Scenario: PB in bottom 25% of industry
- **WHEN** a stock's PB is in the bottom 25% of its industry peers
- **THEN** the PB sub-score SHALL be 3 (maximum possible for PB)

#### Scenario: PB in top quartile
- **WHEN** a stock's PB is in the top 25% of its industry peers
- **THEN** the PB sub-score SHALL be 0

### Requirement: Dividend yield bonus
The system SHALL award a bonus sub-score for stocks with meaningful dividend yield.

#### Scenario: High dividend yield
- **WHEN** a stock's dividend yield exceeds 2%
- **THEN** the dividend sub-score SHALL be 2

#### Scenario: Moderate dividend yield
- **WHEN** a stock's dividend yield is between 1% and 2%
- **THEN** the dividend sub-score SHALL be 1

#### Scenario: No dividend data
- **WHEN** dividend yield data is unavailable
- **THEN** the dividend sub-score SHALL be 0

### Requirement: Valuation score composite
The system SHALL compute valuation_score as the sum of PE, PB, and dividend sub-scores, capped at 10.

#### Scenario: Valuation score present in leaderboard response
- **WHEN** GET /api/v1/market/industry-leaderboard is called
- **THEN** each item SHALL include valuation_score with a value in range [0, 10]
- **AND** the score_detail SHALL include a valuation entry with PE, PB, and dividend sub-scores and their reasons

### Requirement: Industry peers for percentile calculation
The system SHALL use all candidates in the same Shenwan primary industry as the peer group for PE/PB percentile calculation.

#### Scenario: Industry has fewer than 3 candidates
- **WHEN** an industry has fewer than 3 candidates after hard filters
- **THEN** the percentile SHALL be computed against all candidates across all industries as fallback
