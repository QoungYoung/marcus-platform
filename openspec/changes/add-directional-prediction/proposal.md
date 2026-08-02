## Why

The current industry leaderboard scoring model (3-dim: trend, valuation, reversal) achieves IC=0.14 for cross-sectional ranking — it answers "which stock will outperform peers?" reliably. But it cannot answer the question traders actually ask: "which stocks are likely to go UP tomorrow / in 3 days / in 5 days?"

Empirical analysis on 60 trading days of data shows:
- Top-ranked stocks go up only 41% of the time (5-day horizon), identical to random selection
- In the bear market regime, mean 5-day return is negative for ALL score groups
- Market timing (up_ratio) explains 42% of directional signal; stock-level features from the current model contribute <60% combined

The platform needs a dedicated directional prediction service that combines market regime classification with stock-level breakout/catalyst detection, trained as a binary classifier rather than a ranking model.

## What Changes

- **New service**: `DirectionalPredictionService` — ML-based binary classifier predicting P(return > threshold) for 1/3/5-day horizons
- **New features**: Money flow (big order net flow), volume breakout ratios, consecutive up/down streaks, gap detection, limit-up proximity, market breadth indicators — sources not used by the leaderboard model
- **New API endpoint**: `GET /api/v1/direction/predict` returning per-stock up-probability scores with confidence levels
- **New data pipeline**: `scripts/dump_direction_data.py` — collects training data with directional labels (up/down binary) instead of continuous returns
- **Modified scoring model**: Leaderboard service unchanged; directional prediction operates independently with different features and objective

## Capabilities

### New Capabilities
- `direction-prediction-model`: Binary classifier (XGBoost/LogisticRegression) trained on directional labels, walk-forward validated, outputting P(return > 0) for 1d/3d/5d horizons
- `market-regime-classifier`: Pre-filter that classifies each trading day as "favorable" or "unfavorable" for directional trading, using market breadth, index trend, and volatility features
- `direction-feature-engineering`: New feature extraction pipeline including money flow indicators, volume breakout ratios, streak counters, gap patterns, and limit-up/down proximity
- `direction-data-pipeline`: `dump_direction_data.py` script for historical data collection with binary labels, supporting walk-forward training/validation split

### Modified Capabilities
<!-- No existing specs are modified by this change -->

## Impact

- **New files**: `backend/app/services/direction_prediction.py`, `backend/app/api/direction.py`, `scripts/dump_direction_data.py`
- **New data dependencies**: Tushare `moneyflow` API (big/small order net flows), `limit_list` API (limit-up/down data), `daily` API (OHLCV for breakout patterns)
- **Existing files unchanged**: `industry_leaderboard.py`, `dump_scores.py` — directional model runs independently
- **Compute**: Training runs offline (batch); inference is lightweight (single model.predict per stock)
