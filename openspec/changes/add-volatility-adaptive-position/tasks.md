## 1. Data layer — Add ATR and ADX fetch to calc_position

- [x] 1.1 Add `stk_factor_pro(limit=1)` call in `calc_position` to fetch `atr_qfq` and `dmi_adx_qfq` for the target symbol
- [x] 1.2 Wrap the call in try/except with graceful degradation: on failure, default both coefficients to 1.0 and log a warning

## 2. Volatility coefficient

- [x] 2.1 Implement `_get_volatility_coefficient(atr: float, close: float) -> tuple[str, float, float]` returning (tier_label, atr_pct, coefficient) per the 4-tier design
- [x] 2.2 Integrate the volatility coefficient into the `calc_position` multiplier chain (multiply `effective_single_cap` by it)
- [x] 2.3 Add `volatility_level`, `atr_pct`, and `volatility_coef` fields to `CalcPositionResponse` model

## 3. ADX trend strength coefficient

- [x] 3.1 Implement `_get_adx_coefficient(adx: float) -> float` per the 3-tier design
- [x] 3.2 Integrate the ADX coefficient into the `calc_position` multiplier chain
- [x] 3.3 Add `adx` and `adx_coef` fields to `CalcPositionResponse` model

## 4. Minimum position floor

- [x] 4.1 After all multipliers are applied, enforce a minimum single-stock cap of 2% of total asset
- [x] 4.2 Add a check: if the floored cap buys < 100 shares, include a warning recommending to skip the position

## 5. Response and logging

- [x] 5.1 Populate all new response fields in the `CalcPositionResponse` return value (main + backtest sandbox)
- [x] 5.2 Add warnings to the response when coefficients are active (volatility reduction, ADX reduction, floor applied)
- [x] 5.3 Log the coefficient values at INFO level for auditability

## 6. Verification

- [ ] 6.1 Manual test: call `/api/v1/indicator/calc-position` with a real symbol and verify the response includes all new fields
- [ ] 6.2 Manual test: verify that a high-volatility, low-ADX stock gets a significantly smaller position than a low-volatility, high-ADX stock with all other parameters equal
