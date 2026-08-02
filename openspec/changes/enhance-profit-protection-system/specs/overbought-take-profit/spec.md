## ADDED Requirements

### Requirement: Overbought early warning with 30% reduction
The system SHALL monitor KDJ_K values for all open positions and trigger a 30% position reduction when KDJ_K first crosses above 80.

#### Scenario: KDJ_K crosses above 80 for the first time
- **WHEN** a held position's KDJ_K value rises above 80 for the first time since the position was opened or since the overbought counter was reset
- **THEN** the system SHALL trigger a sell order for 30% of the available (non-T+1 locked) shares
- **AND** the reason SHALL be recorded as "超买止盈-预警: KDJ_K={value}≥80, 减仓30%"

#### Scenario: KDJ_K stays above 80 on consecutive checks same day
- **WHEN** KDJ_K ≥ 80 was already triggered earlier in the same trading day
- **AND** the 30% reduction was already executed
- **THEN** the system SHALL NOT trigger another 30% reduction for the same condition on the same day

#### Scenario: KDJ_K below 80
- **WHEN** a held position's KDJ_K value is below 80
- **THEN** the system SHALL NOT trigger any overbought take-profit action

### Requirement: Overbought plus surge with 50% reduction
The system SHALL trigger a 50% position reduction when KDJ_K ≥ 80, RSI6 ≥ 75, and the single-day price increase exceeds 3% simultaneously.

#### Scenario: All three conditions met
- **WHEN** a held position's KDJ_K ≥ 80
- **AND** RSI6 ≥ 75
- **AND** the current day's price change exceeds +3%
- **THEN** the system SHALL trigger a sell order for 50% of the available shares
- **AND** the reason SHALL be recorded as "超买止盈-急涨: KDJ_K={k}≥80, RSI6={r}≥75, 涨幅{chg}%>3%, 减仓50%"

#### Scenario: KDJ and RSI overbought but daily change below threshold
- **WHEN** KDJ_K ≥ 80 and RSI6 ≥ 75
- **AND** the current day's price change is ≤ 3%
- **THEN** only the first-tier 30% reduction SHALL apply (if not already executed)

#### Scenario: Higher priority than first-tier warning
- **WHEN** both the first-tier warning (KDJ_K ≥ 80) and the second-tier surge conditions are met simultaneously
- **THEN** the second-tier 50% reduction SHALL take precedence

### Requirement: Three consecutive overbought days force liquidation
The system SHALL force-liquidate the entire position when KDJ_K has been ≥ 80 for three consecutive trading days.

#### Scenario: Third consecutive day of KDJ_K ≥ 80
- **WHEN** KDJ_K ≥ 80 is detected
- **AND** the previous two trading days also had KDJ_K ≥ 80
- **THEN** the system SHALL trigger a sell order for 100% of the available shares
- **AND** the reason SHALL be recorded as "超买止盈-清仓: KDJ_K连续3日≥80, 强制清仓"

#### Scenario: Two consecutive days with gap on third
- **WHEN** KDJ_K ≥ 80 was detected on day 1 and day 2
- **AND** day 3 KDJ_K < 80
- **THEN** the consecutive day counter SHALL reset
- **AND** no force liquidation SHALL be triggered

#### Scenario: Weekend gap does not reset counter
- **WHEN** KDJ_K ≥ 80 was detected on Friday
- **AND** the next trading day (Monday) also has KDJ_K ≥ 80
- **THEN** the consecutive day counter SHALL increment to 2
- **AND** the natural day gap of 2-3 days SHALL NOT reset the counter

### Requirement: Overbought indicator data retrieval
The system SHALL provide a function to retrieve current KDJ_K, RSI6, and daily change percentage for a given symbol using real-time and historical market data.

#### Scenario: Real-time data available
- **WHEN** the Tencent real-time quote is available for the symbol
- **AND** historical Tushare stk_factor_pro data is available
- **THEN** the function SHALL return KDJ_K and RSI6 values computed from the real-time estimate algorithm
- **AND** daily_change_pct SHALL be from the real-time quote

#### Scenario: Real-time data unavailable — fallback to historical
- **WHEN** the Tencent real-time quote is unavailable
- **THEN** the function SHALL return KDJ_K and RSI6 from the most recent Tushare stk_factor_pro confirmed values
- **AND** daily_change_pct SHALL be computed from the two most recent daily close prices

#### Scenario: Cache hit within 60 seconds
- **WHEN** the function was called for the same symbol within the last 60 seconds
- **THEN** the cached result SHALL be returned without re-fetching from external APIs

### Requirement: Overbought rule priority within stop-loss chain
The overbought take-profit rule (Rule 2.3) SHALL be evaluated after Iron Rule 2 and before Technical Divergence (Rule 2.5) in the stop-loss rule chain.

#### Scenario: Both Iron Rule 2 and overbought take-profit trigger
- **WHEN** Iron Rule 2 determines a stop-loss is needed (price already declining)
- **AND** overbought take-profit conditions are also met
- **THEN** Iron Rule 2 SHALL take precedence and execute first

#### Scenario: Overbought triggers without Iron Rule 2
- **WHEN** KDJ_K ≥ 80 and all overbought conditions are met
- **AND** Iron Rule 2 does not trigger (price still within protection lines)
- **THEN** the overbought take-profit SHALL execute the appropriate reduction
