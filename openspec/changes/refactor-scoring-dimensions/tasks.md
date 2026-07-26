## 1. Historical Candidate Selection

- [x] 1.1 Add `_get_industry_candidates_historical(self, date: str)` method to `IndustryLeaderboardService` that reconstructs top-3-by-market-cap per industry using `daily_basic` table `total_mv` for the given date
- [x] 1.2 Add fallback logic: when daily market cap is unavailable for a date, use current `stock_pool` data and set `survivorship_bias: true` flag in response
- [x] 1.3 Wire historical candidate selection into `get_leaderboard()` — when `date` parameter is provided, use `_get_industry_candidates_historical(date)` instead of `_get_industry_candidates()`
- [x] 1.4 Update `dump_scores.py` to accept `--limit 0` for "all candidates" mode, change default to remain 30 for backward compatibility

## 2. Risk/Pricing Family

- [x] 2.1 Add `_compute_risk_score(self, candidates, indicators)` method: continuous 0-10 score from RSI6 z-score (0-4 pts), Bollinger %B (0-3 pts), and 5-day reversal probability (0-3 pts), normalized within daily cross-section
- [x] 2.2 Rewrite `_compute_price_residual(self, candidates, quotes, indicators, daily_bars, regime)`: replace absolute daily gain sub-score with industry residual return (stock_pct - industry_avg_pct)
- [x] 2.3 Update composite score calculation in `get_leaderboard()` to use risk_score and rewritten price_residual_score; keep `overbought_score` as deprecated field (always 0)
- [x] 2.4 Update `_apply_penalties()` to reference the new risk_score dimension instead of overbought_score; use risk_score < 2 as overheat trigger

## 3. Fundamental Anchor Family

- [x] 3.1 Add `_compute_valuation_anchor(self, candidates, indicators)` method: PE percentile within industry (0-5 pts) + PB percentile placeholder (0-3 pts) + dividend yield placeholder (0-2 pts), capped at 10
- [x] 3.2 Handle missing PE data: impute with industry median (2.5) and global fallback
- [x] 3.3 Add fallback peer group: when industry has <3 candidates, compute percentile against all candidates
- [x] 3.4 Wire valuation_score into composite score calculation and response schema

## 4. Reversal Signal

- [x] 4.1 Add `_compute_reversal_signal(self, candidates, indicators, daily_bars)` method: capitulation volume detect (0-4 pts) + mean-reversion distance (0-4 pts) + quality filter bonus (2 pts), capped at 10
- [x] 4.2 Implement quality filter: market_cap > industry median AND 20-day return > -20% as eligibility checks; failing either → reversal_score = 0
- [x] 4.3 Wire reversal_score into composite score calculation and response schema

## 5. Response Schema & API Updates

- [x] 5.1 Add `valuation_score`, `reversal_score`, `risk_score` fields to leaderboard item response in `get_leaderboard()`
- [x] 5.2 Add deprecated `overbought_score` (always 0) alongside legacy `price_residual_score` (updated with residual returns)
- [x] 5.3 Add `score_families` metadata object to response: `{trend_quality: [...], relative_strength: [...], risk_pricing: [...], fundamental_anchor: [...]}` with family weights
- [x] 5.4 Support `sort_by=valuation_score`, `sort_by=reversal_score`, `sort_by=risk_score` in the sort parameter map
- [x] 5.5 Add `survivorship_bias` boolean field to response (true when fallback candidate selection was used)

## 6. Validation & Tuning

- [ ] 6.1 Run `dump_scores.py --days 60 --limit 0 --output data/scores_v2.csv` to generate full cross-section CSV with new dimensions (long-running, ~2 hours)
- [ ] 6.2 Run `compare_linear_vs_xgb.py` on new CSV, compare per-dimension IC against baseline
- [ ] 6.3 Run `optimize_weights.py` with updated DIMS list (8 dimensions) to get new optimal weights
- [ ] 6.4 Compare walk-forward IC of new composite vs old composite across all windows
- [ ] 6.5 If new composite IC_mean > old by 0.02+, declare success; otherwise diagnose per-dimension IC and iterate on weak dimensions

## 7. Cleanup (post-validation)

- [x] 7.0 移除5个死维度（volume_price/industry_relative/price_residual/risk/capital），保留trend+valuation+reversal 3维。走步前进验证IC从0.039提升至0.095 (+0.056)，权重搜索确认死维权重<0.01

- [ ] 7.1 Remove deprecated `overbought_score` field from response (if validation passes)
- [ ] 7.2 Remove legacy `overbought_score` computation, keep only risk_score
- [ ] 7.3 Update frontend `LeaderboardItem` TypeScript interface to use new field names
- [ ] 7.4 `optimize_weights.py` DIMS and DIM_CN already updated to 8-dimension set
