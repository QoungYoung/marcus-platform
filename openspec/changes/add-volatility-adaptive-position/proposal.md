## Why

The current position sizing in `calc_position` uses a fixed signal-strength-based cap (10-25%) with oscillation-tightening (min 8%). But it ignores two quantifiable risk dimensions that directly affect expected drawdown: **volatility** and **trend strength**. A stock with 6% daily volatility carries double the stop-out risk of a 3% stock at the same position size, yet the system allocates identically. The data for both adjustments (ATR and ADX) is already available via `stk_factor_pro` — it's just not wired into the position calculation.

## What Changes

- **P0 — Volatility-adaptive position sizing**: Multiply the computed single-stock cap by an ATR-based volatility coefficient. Higher volatility → smaller position, lower volatility → unchanged or slightly larger. ATR/price ratio is already fetched in existing `get_stock_factors` calls.
- **P1 — ADX trend strength multiplier**: Multiply the single-stock cap by an ADX-based trend strength coefficient. Strong trend (ADX>40) → full allocation, weak trend (ADX 25-40) → 0.8x, no trend (ADX<25) → oscillation rules already handle this case.
- Add a **minimum position floor** (2% of total asset) to prevent the compounded multiplier chain from producing unusably small allocations.

## Capabilities

### New Capabilities
- `volatility-adaptive-position`: Single-stock position cap adjusted inversely to realized volatility (ATR/price ratio), capping risk exposure in high-volatility names.
- `adx-trend-strength`: ADX trend strength factor integrated into the position sizing multiplier chain, scaling position size with trend conviction.

### Modified Capabilities
- None (existing specs cover trade execution, not position sizing logic)

## Impact

- `backend/app/api/indicator.py` — `calc_position()` (`calc_position`: around line 1431): add ATR fetch + volatility coefficient + ADX fetch + trend strength coefficient + minimum floor to the position calculation chain
- `backend/app/api/market.py` — `get_stock_factors()`: already returns `atr_qfq` and `dmi_adx_qfq`; no changes needed but verify field availability
- `backend/app/models/indicator.py` — `CalcPositionResponse`: add new output fields (`volatility_tier`, `adx`, `volatility_coefficient`, `adx_coefficient`) to the response model
