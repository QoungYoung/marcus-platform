## ADDED Requirements

### Requirement: ADX trend strength coefficient
The system SHALL adjust the single-stock position cap based on the stock's ADX trend strength indicator. Stronger trends SHALL allow larger positions; weaker trends SHALL reduce positions.

The coefficient tiers are:
- ADX > 40 → coefficient 1.0 (strong trend, full allocation)
- ADX 25-40 → coefficient 0.8 (weak trend, 80% allocation)
- ADX < 25 → coefficient 0.6 (no trend, 60% allocation)

#### Scenario: Strong trend stock gets full allocation
- **WHEN** calc_position is called for a stock with ADX value 45
- **THEN** the ADX coefficient is 1.0 and the position cap is unchanged by trend strength

#### Scenario: Weak trend stock gets reduced allocation
- **WHEN** calc_position is called for a stock with ADX value 32
- **THEN** the ADX coefficient is 0.8 and the position cap is multiplied by 0.8

#### Scenario: No-trend stock gets further reduced allocation
- **WHEN** calc_position is called for a stock with ADX value 18
- **THEN** the ADX coefficient is 0.6 and the position cap is multiplied by 0.6

#### Scenario: ADX data unavailable degrades gracefully
- **WHEN** calc_position is called but the stk_factor_pro call fails or returns no ADX value
- **THEN** the ADX coefficient defaults to 1.0 (no adjustment) and a warning is logged

### Requirement: ADX data surfaced in response
The system SHALL include the raw ADX value and applied coefficient in the calc_position API response.

#### Scenario: Response includes ADX fields
- **WHEN** calc_position returns a response
- **THEN** the response includes `adx` (float) and `adx_coefficient` (float)

### Requirement: Minimum position floor
The system SHALL enforce a minimum single-stock position cap of 2% of total asset after all coefficients are applied, unless the resulting share count is below 100 shares (1 lot).

#### Scenario: Compounded multipliers hit floor
- **WHEN** the product of all position coefficients yields a cap below 2% of total asset
- **THEN** the cap is floored at 2% and a warning is included in the response

#### Scenario: Floor still below 1 lot triggers skip
- **WHEN** the 2% floored cap buys fewer than 100 shares at current price
- **THEN** the response includes a warning recommending to skip this position
