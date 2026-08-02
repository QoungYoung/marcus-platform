## ADDED Requirements

### Requirement: Volatility-adaptive position coefficient
The system SHALL adjust the single-stock position cap inversely to the stock's realized volatility, measured as ATR/close ratio. Higher volatility SHALL reduce the position cap; lower volatility SHALL leave it unchanged.

The coefficient tiers are:
- ATR/close < 2% → coefficient 1.0 (full)
- ATR/close 2-4% → coefficient 0.85
- ATR/close 4-6% → coefficient 0.7
- ATR/close ≥ 6% → coefficient 0.5

#### Scenario: Low volatility stock gets full allocation
- **WHEN** calc_position is called for a stock with ATR/close ratio 1.5%
- **THEN** the volatility coefficient is 1.0 and the position cap is unchanged by volatility

#### Scenario: High volatility stock gets reduced allocation
- **WHEN** calc_position is called for a stock with ATR/close ratio 5.2%
- **THEN** the volatility coefficient is 0.7 and the position cap is multiplied by 0.7

#### Scenario: Extreme volatility stock gets halved allocation
- **WHEN** calc_position is called for a stock with ATR/close ratio 7.0%
- **THEN** the volatility coefficient is 0.5 and the position cap is multiplied by 0.5

#### Scenario: ATR data unavailable degrades gracefully
- **WHEN** calc_position is called but the stk_factor_pro call fails or returns no ATR value
- **THEN** the volatility coefficient defaults to 1.0 (no adjustment) and a warning is logged

### Requirement: Volatility data surfaced in response
The system SHALL include the volatility tier, ATR/close ratio, and applied coefficient in the calc_position API response.

#### Scenario: Response includes volatility fields
- **WHEN** calc_position returns a response
- **THEN** the response includes `volatility_tier` (string: "低波"/"中波"/"高波"/"极高"), `atr_pct` (float), and `volatility_coefficient` (float)
