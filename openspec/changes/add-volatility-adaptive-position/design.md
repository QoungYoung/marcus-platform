## Context

`calc_position` (`indicator.py:1431`) computes position size through a multiplier chain: signal strength cap (10-25%) × role cap × total cap (stance) × oscillation tightening (min with 8%/50%) × downgrade multiplier (from `check_entry_filters`). Each layer can only reduce the position, never increase it.

Two risk dimensions are missing from this chain:
- **Volatility**: Higher ATR → higher probability of hitting stop loss at the same position size
- **Trend strength**: Weak trend → higher probability of whipsaw/ false breakout

Both data points are available via Tushare `stk_factor_pro` (`atr_qfq`, `dmi_adx_qfq`), which `calc_position` does not currently call.

## Goals / Non-Goals

**Goals:**
- Add volatility coefficient: position size ∝ 1 / volatility, capped at [0.5, 1.0]
- Add ADX trend strength coefficient: position size ∝ trend conviction, capped at [0.6, 1.0]
- Add minimum position floor (2%) so compounding multipliers don't produce unusable allocations
- Surface the new coefficients in the API response for transparency

**Non-Goals:**
- Do NOT change the multiplier chain architecture (no weighted scoring, no mutual compensation between layers)
- Do NOT add range/oscillation trading strategies
- Do NOT replace the 5-indicator market regime voting with ADX
- Do NOT modify `check_entry_filters` or `stop_loss_monitor`

## Decisions

### Decision 1: Direct `stk_factor_pro` call in `calc_position` (not via `get_stock_factors`)

**Rationale**: `calc_position` currently calls `pro.daily()` directly for kline-derived amplitude. Adding a `stk_factor_pro(limit=1)` call is the minimal change. We don't need to modify the shared `TechnicalData` model or `get_stock_factors` endpoint, which serve API consumers. One extra Tushare call per position calculation is negligible — `calc_position` runs on-demand during trade decisions, not in a polling loop.

**Alternative considered**: Extend `get_stock_factors` to return ADX and call it from `calc_position`. Rejected because `get_stock_factors` returns multiple days of history (unnecessary overhead) and modifying it risks breaking API consumers. The model change alone (adding `dmi_adx` to `TechnicalData`) would be a breaking change for clients that don't expect the new field.

### Decision 2: Volatility coefficient formula — `ATR / close * 100` with tiered caps

```
atr_pct = ATR / close * 100

if atr_pct < 2:      coef = 1.0    (low vol, full position)
elif atr_pct < 4:    coef = 0.85   (normal vol)
elif atr_pct < 6:    coef = 0.7    (elevated vol)
else:                coef = 0.5    (high vol, half position)
```

**Rationale**: ATR/price normalizes across price levels. Tiered thresholds (rather than continuous formula) make behavior predictable and debuggable. The floor at 0.5 prevents the coefficient from shrinking positions to zero — at some point the position is already small enough that stop-loss width handles the remaining risk.

**Alternative considered**: Continuous `coef = 3.0 / atr_pct` formula. Rejected because edge cases (atr_pct → 0) need clamping anyway, and tiered buckets are easier to reason about in backtest analysis.

### Decision 3: ADX coefficient formula — tiered by Tushare `dmi_adx_qfq` value

```
adx = dmi_adx_qfq (0-100 scale)

if adx > 40:    coef = 1.0    (strong trend, full allocation)
elif adx > 25:  coef = 0.8    (weak trend, 80% allocation)
else:           coef = 0.6    (no trend, 60% allocation — oscillation rules already tighten separately)
```

**Rationale**: ADX < 25 means the oscillation tightening in `calc_position` (single cap ≤ 8%, total ≤ 50%) already applies. The ADX coefficient here is an additional layer: even within oscillation regime, a stock with ADX 22 (almost trending) deserves more allocation than one with ADX 12 (completely directionless). The 0.6 floor on ADX coefficient preserves this gradient while still penalizing directionless stocks.

The oscillation tightening and ADX coefficient are multiplicative but serve different purposes:
- Oscillation tightening: macro market regime → caps total exposure
- ADX coefficient: per-stock trend conviction → adjusts individual position size

**Alternative considered**: Skip ADX and rely solely on the 5-indicator market regime voting. Rejected because the voting system answers "what market are we in?" while ADX answers "how strong is this stock's trend?" — different questions, both useful.

### Decision 4: Minimum position floor at 2% of total asset

After all multipliers are applied, if the resulting single-stock cap < 2% of total asset, floor it at 2%. If even 2% buys fewer than 100 shares (1 lot), the position is skipped with a warning.

**Rationale**: The multiplier chain is now 6 layers deep. Without a floor, the product of multiple conservative coefficients can produce allocations like 0.8% which buy < 1 lot. A 2% floor is small enough to be safe but large enough to be meaningful on a 1M portfolio (20,000 RMB, ~5-8 lots of a typical 20-30 RMB stock).

### Decision 5: New coefficients are multiplicative, not additive

```
final_cap = base_cap × oscillation × downgrade × volatility_coef × adx_coef
```

Not: `final_cap = base_cap × (1 - penalties_sum)`. This preserves the existing architecture — each layer independently scales the result, and the order doesn't matter (multiplication is commutative).

## Risks / Trade-offs

- **[Risk] Additional Tushare API call may timeout** → `stk_factor_pro` with `limit=1` is fast (< 2s typical). Wrap in try/except; on failure, default coefficients to 1.0 (no adjustment) and log a warning — graceful degradation.
- **[Risk] ADX is a lagging indicator (14-period)** → It won't catch the first 1-3 days of a new trend. This is acceptable for a position sizing coefficient (not an entry signal). By day 5-7 of a trend, ADX will have caught up and the coefficient will scale up accordingly.
- **[Risk] ATR can spike on gap days** → A single gap day inflates the 20-period ATR marginally (1/20 weight). Not a concern at our tier granularity (4 buckets).
- **[Trade-off] More coefficients = more opacity** → Mitigated by surfacing all coefficients in the API response (`volatility_tier`, `volatility_coefficient`, `adx`, `adx_coefficient`), making the math auditable.

## Open Questions

None — both formulas and thresholds are specified above and ready for implementation.
