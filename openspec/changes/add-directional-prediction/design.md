## Context

The current leaderboard scoring model (3-dim: trend, valuation, reversal) produces cross-sectional rankings with IC=0.14. However, analysis on 60 trading days shows it has zero directional prediction power: top-ranked stocks go up only ~40% of the time, identical to random selection. The model answers "which stock will outperform peers?" — not "which stock will go up?"

This project builds a separate, purpose-built directional prediction service. The key insight from exploratory analysis: **market timing explains 42% of directional signal** (via `up_ratio` feature importance in XGBoost), while stock-level features from the current scoring model contribute marginally. The directional model must therefore combine market regime classification with stock-level breakout/catalyst features that the leaderboard model doesn't use.

## Goals / Non-Goals

**Goals:**
- Binary classifier predicting P(day5_pct > 0) and P(day1_pct > 0), P(day3_pct > 0) for each stock
- Market regime pre-filter: classify each day as "favorable" or "unfavorable" before stock-level scoring
- New feature set optimized for directional prediction: money flow, volume breakout, streak patterns, gap detection, market breadth
- Walk-forward validation comparing ML classifier accuracy vs baseline (always-guess-majority)
- Standalone service, independent from industry leaderboard — different features, different objective

**Non-Goals:**
- Not modifying the leaderboard service or its API
- Not real-time streaming — batch prediction at market close for next-day signals
- Not predicting return magnitude — binary classification only (up/down)
- Not incorporating news/sentiment NLP (no data source)
- Not replacing the existing backtest engine

## Decisions

### Decision 1: Two-stage architecture — Regime filter → Stock classifier

```
Market Close Data
       │
       ▼
┌─────────────────────┐
│ Stage 1: Regime     │  ← market breadth, index trend, limit-up count
│ Classifier           │     output: favorable / unfavorable
└──────┬──────────────┘
       │
       ▼  (only if favorable)
┌─────────────────────┐
│ Stage 2: Stock      │  ← money flow, volume breakout, streaks, gap
│ Direction Classifier│     output: P(up) per stock per horizon
└─────────────────────┘
```

**Rationale**: XGBoost feature importance shows `up_ratio` (market breadth) at 42%. In bear markets, stock-level features are overwhelmed by market direction. Separating the two stages (1) prevents the model from learning spurious stock-market interactions, and (2) allows independent tuning of market timing vs stock selection.

**Alternatives considered**:
- Single model with market features as inputs: Already tested — `market_up` gets 0% importance because `up_ratio` captures all the signal. Stock features get drowned. Rejected.
- Multi-target regression: Predicting return magnitude is harder than direction. Binary classification is a simpler, more robust objective. Rejected for v1.

### Decision 2: Target definition — Binary up/down with threshold

Target variables for three horizons:
- `target_1d`: day1_pct > 0 (1 if positive, 0 otherwise)
- `target_3d`: day3_pct > 0
- `target_5d`: day5_pct > 0

Three separate binary classifiers, one per horizon. Each returns a probability score [0, 1].

**Rationale**: Different horizons have different signal sources. Short-term (1d) is dominated by money flow and gap patterns. Medium-term (5d) is more influenced by trend and volume. Separate models allow feature importance to differ per horizon.

**Alternatives considered**:
- Single multi-output classifier: Shared feature importance across horizons would mask horizon-specific signals. Rejected.
- Threshold > 1% or > 2%: Reduces positive samples from ~40% to ~25%, making the classification problem even more imbalanced. Stick with > 0 for v1.

### Decision 3: Feature set — Directional signals not in leaderboard

New features not currently used by `industry_leaderboard.py`:

| Category | Features | Data Source |
|----------|----------|-------------|
| Money Flow | big_order_net, small_order_net, main_force_ratio | Tushare `moneyflow` |
| Volume Breakout | vol_ratio_5d, vol_ratio_20d, amount_breakout | Tushare `daily` |
| Streak Patterns | consecutive_up_days, consecutive_down_days | Derived from `daily` |
| Gap Patterns | gap_up_pct, gap_down_pct, gap_fill_distance | Derived from `daily` OHLC |
| Market Breadth | up_ratio, limit_up_count, advance_decline, mkt_vol_trend | Derived from candidate cross-section |
| Index Features | index_ret_5d, index_rsi, index_ma_deviation | Tushare `index_daily` |

Existing leaderboard dimensions (trend_score, valuation_score, reversal_score) are included as baseline features but given lower weight.

**Rationale**: The leaderboard dimensions were selected for cross-sectional ranking, not direction. Money flow captures capital flows (buying pressure), volume breakout captures attention/participation, streaks capture momentum persistence — these are directionally predictive in ways that PE valuation is not.

### Decision 4: Model selection — XGBoost classifier with sample weights

Primary model: XGBoost classifier with `scale_pos_weight` to handle class imbalance (~40% positive).
Baseline comparison: Logistic regression (linear benchmark), Random Forest (nonlinear benchmark).

Walk-forward validation identical to `compare_linear_vs_xgb.py` pattern: expanding window, train=30 days, test=3 days step.

Success criterion: Classifier accuracy exceeds baseline (always-guess-majority) by 5%+ on walk-forward test windows.

**Alternatives considered**:
- Deep learning (MLP): Requires much more data than 60 trading days. Overfitting risk. Rejected for v1.
- LightGBM: Similar to XGBoost. Pick XGBoost for consistency with existing codebase patterns.

### Decision 5: Data pipeline — Separate script from dump_scores.py

New script `scripts/dump_direction_data.py` that:
1. Iterates trading days (same as `dump_scores.py`)
2. Fetches `moneyflow` (batch by date, not stock), `daily` (OHLCV), `stk_factor` (technical indicators), `index_daily` (market index)
3. Derives features: vol_ratio, streaks, gaps, breadth indicators
4. Merges forward returns, creates binary labels
5. Outputs CSV with features + targets per stock per date

Separate from `dump_scores.py` because:
- Different data sources (moneyflow especially)
- Different output schema (features + binary labels, not scores)
- Can run independently or in parallel with leaderboard data collection

## Risks / Trade-offs

- **[Risk] Money flow data quality**: Tushare `moneyflow` may have limited history or rate limits. Mitigation: Use `daily` volume as fallback — amount × close approximates total flow; big order flow can be imputed from price impact.
- **[Risk] Overfitting with 60-day history**: Walk-forward with only 10 test windows may give unstable estimates. Mitigation: Keep model simple (max_depth=3, reg_alpha=0.1). Expand training data over time.
- **[Risk] Regime classifier false positives**: Predicting market direction is itself a hard problem (~55% accuracy at best). Mitigation: Regime filter is a soft gate, not a hard block — "unfavorable" days still get predictions but with a confidence penalty.
- **[Trade-off] Binary vs probability calibration**: XGBoost probabilities may not be well-calibrated (P=0.6 doesn't mean truly 60% chance). Mitigation: Use IsotonicRegression calibration on validation set.

## Open Questions

- Q: Should the direction prediction run as a daemon (auto-refresh) or on-demand API?
  - Tentative: On-demand API for initial requests, with optional cache TTL for repeated queries within the same trading session.
- Q: Three separate classifiers or one multi-output?
  - Tentative: Three separate classifiers (Decisions section). Validate if multi-output performs comparably before finalizing.
- Q: What's the minimum confidence threshold for a "buy" signal?
  - Tentative: P > 0.55 (must beat baseline by meaningful margin). Tune after calibration.
