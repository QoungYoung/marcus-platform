## Why

The current 6-dimension scoring model for the industry leaderboard was empirically validated via walk-forward backtesting and found to have near-zero (often negative) out-of-sample IC. Root cause analysis revealed the dimensions are not independent signals — five of six are correlated momentum variants ("buy what went up"), the sixth ("overbought risk") almost never fires, and the "price residual" dimension is misnamed (40% of its score comes from absolute daily gain, making it another momentum factor). Additionally, historical backtesting uses the current `stock_pool.db` snapshot for candidate selection, introducing survivorship bias. The scoring model needs structural redesign, not weight tuning.

## What Changes

- **Redesign dimension taxonomy**: Replace the single-axis "5 momentum + 1 phantom" layout with 4 signal families (Trend Quality, Volume-Price, Relative Strength, Risk/Pricing), each containing orthogonal sub-dimensions
- **Fix Price Residual**: Rewrite as a true residual/risk dimension — crash recovery detection, mean-reversion potential, and fair-value deviation — removing the absolute daily gain component that made it a momentum clone
- **Replace overbought_score with continuous risk dimension**: Drop the binary RSI/KDJ dual-confirm gate (which fires on <1% of samples) in favor of a z-score-based composite risk signal covering RSI, Bollinger Band position, and short-term reversal probability
- **Add reversal/correction-capture dimension**: A counter-trend signal that identifies oversold quality stocks (valuation compression + capitulation volume + mean-reversion setup), providing the missing contrarian signal source
- **Add valuation anchor dimension**: PE/PB percentile within industry (not a hard >200 filter, but a continuous score rewarding reasonable valuation), giving the model a fundamental anchor
- **Time-aware historical candidate selection**: Load daily `stock_pool` snapshots (or reconstruct from `daily` market cap data) so historical backtesting uses candidates as they existed on each date, eliminating survivorship bias
- **Expand cross-section for validation**: Bump the daily sample from top 30 to all eligible candidates (typically 200-330), reducing Spearman standard error from ~0.19 to ~0.06

## Capabilities

### New Capabilities
- `scoring-dimensions`: Redesigned score dimension model with truly independent signal families (Trend Quality, Volume-Price, Relative Strength, Risk/Pricing) and a continuous risk assessment framework
- `valuation-anchor`: PE/PB percentile-based scoring within industry as a fundamental anchor dimension
- `reversal-signal`: Contrarian dimension capturing oversold quality stocks with capitulation and mean-reversion characteristics
- `historical-candidate-selection`: Time-aware candidate selection that reconstructs industry top-3 by market cap for each historical date

### Modified Capabilities
- `market`: Industry leaderboard scoring now uses the redesigned dimension taxonomy; the `GET /api/v1/market/industry-leaderboard` response schema adds new dimension fields (`valuation_score`, `reversal_score`, `risk_score`) and deprecates `overbought_score` and `price_residual_score` in their current form

## Impact

- `backend/app/services/industry_leaderboard.py`: Major refactor — replace 5 scoring methods with redesigned ones, remove `_compute_overbought_risk`, add `_compute_valuation_anchor`, `_compute_reversal_signal`, rewrite `_compute_price_residual` as true risk dimension
- `backend/app/models/market.py`: Update `LeaderboardItem` schema with new fields
- `scripts/dump_scores.py`: Support time-aware candidate selection and expanded cross-section
- `scripts/compare_linear_vs_xgb.py`: Already exists, will be the validation tool
- `openspec/changes/add-industry-leaderboard/specs/industry-leaderboard/spec.md`: Delta spec for the scoring model will reference the redesigned dimensions
- **BREAKING**: Response schema for `/api/v1/market/industry-leaderboard` changes — `overbought_score` removed, `price_residual_score` semantics changed, new fields added. Existing frontend consumers of these fields need updating.
