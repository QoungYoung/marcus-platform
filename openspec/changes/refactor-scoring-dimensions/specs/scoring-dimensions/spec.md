## ADDED Requirements

### Requirement: Four-family scoring taxonomy
The system SHALL organize scoring dimensions into four orthogonal signal families: Trend Quality, Relative Strength, Risk/Pricing, and Fundamental Anchor.

#### Scenario: Families have independent signal characteristics
- **WHEN** cross-family inter-correlation is measured on historical data
- **THEN** the mean absolute pairwise Spearman correlation between families SHALL be lower than within-family correlation

#### Scenario: Each family has at least one dimension
- **WHEN** the scoring pipeline executes
- **THEN** each of the four families SHALL contribute at least one non-zero sub-score to the composite

### Requirement: Trend Quality family
The system SHALL include a Trend Quality family comprising trend_score (MA alignment + MACD + ADX) and volume_price_score (volume-price match + breakout volume + pullback health).

#### Scenario: Trend Quality dimensions retained
- **WHEN** the leaderboard is computed
- **THEN** trend_score and volume_price_score SHALL use the existing computation logic without modification

### Requirement: Relative Strength family
The system SHALL include a Relative Strength family comprising industry_relative_score (5-day and 20-day excess return + turnover contribution) and capital_score (main_net/market_cap + main_pct + d5_main_net).

#### Scenario: Industry relative removes 1-day excess
- **WHEN** industry_relative_score is computed
- **THEN** the 1-day excess return sub-score SHALL be removed
- **AND** a 20-day excess return sub-score (0-6 pts for trending, 0-7 for ranging) SHALL be added

#### Scenario: Capital persistence retained
- **WHEN** capital_score is computed
- **THEN** the existing Round 2 moneyflow fetching and scoring logic SHALL be preserved

### Requirement: Risk/Pricing family
The system SHALL include a Risk/Pricing family comprising risk_score (continuous composite) and price_residual_score (true residual returns).

#### Scenario: Risk score replaces overbought score
- **WHEN** the leaderboard response is generated
- **THEN** risk_score SHALL replace overbought_score
- **AND** risk_score SHALL be a continuous 0-10 value where higher indicates healthier (less overbought)

#### Scenario: Risk score always provides signal
- **WHEN** risk_score is computed for any stock
- **THEN** the score SHALL be non-zero for at least 80% of candidates
- **AND** the cross-sectional standard deviation SHALL exceed 1.0 across candidates on each date

#### Scenario: Price residual uses model residual
- **WHEN** price_residual_score is computed
- **THEN** the "excess gain" sub-score SHALL use the OLS residual of stock return regressed on market return and industry return
- **AND** the absolute daily gain component SHALL be removed

### Requirement: Composite score with family weights
The system SHALL compute composite_score as the weighted sum of all dimensions, grouped by family.

#### Scenario: Composite score includes all families
- **WHEN** composite_score is computed
- **THEN** all four families SHALL contribute to the final score
- **AND** the formula SHALL be documented in the response metadata

#### Scenario: Deprecated fields still returned
- **WHEN** the leaderboard response is generated during the migration period
- **THEN** the old overbought_score field SHALL still be returned with a deprecation notice
- **AND** the old price_residual_score field SHALL still be returned with its legacy computation
