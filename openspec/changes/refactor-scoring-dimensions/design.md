## Context

The current 6-dimension scoring model (`industry_leaderboard.py`) was walk-forward tested and found to produce near-zero out-of-sample IC. The root cause is structural: five of six dimensions measure nearly the same thing (momentum), and the sixth (overbought) is a binary gate that almost never activates. There is no signal diversification.

The existing architecture is a two-round pipeline:
1. Round 1: ~330 candidates scored on 5 dimensions via 3 batch API calls (Tencent qt + Tushare stk_factor_pro + Tushare daily)
2. Round 2: Top 10 re-scored with real-time capital flow from East Money

This change preserves the two-round architecture and data sources, but replaces the dimension taxonomy and scoring logic inside Round 1.

## Goals / Non-Goals

**Goals:**
- Redesign dimensions into 4 orthogonal signal families: Trend Quality, Volume-Price, Relative Strength, Risk/Pricing
- Replace `price_residual_score` (which is 40% momentum) with a true residual/risk dimension
- Replace binary `overbought_score` (RSI>74 && KDJ>100 gate) with a continuous z-score-based risk composite
- Add `valuation_score` (PE/PB percentile within industry) as a fundamental anchor
- Add `reversal_score` (capitulation volume + oversold + mean-reversion setup) as a contrarian signal
- Time-aware historical candidate selection: reconstruct top-3-by-cap per date from daily data
- Expand `dump_scores.py` default cross-section from 30 to all eligible candidates (~200-330)

**Non-Goals:**
- Not changing the two-round architecture (Round 1 batch + Round 2 top-N capital)
- Not changing data sources (Tushare, Tencent, East Money)
- Not adding real-time intra-day computation
- Not touching frontend UI (only response field names)
- Not touching `_compute_trend_composite` or `_compute_volume_price` logic (these dimensions are the strongest signal carriers and are left intact)

## Decisions

### Decision 1: 4-family taxonomy replaces 6-dimension flat list

```
BEFORE (6 dimensions, 5 redundant)          AFTER (7 dimensions, 4 families)
─────────────────────────────────────       ─────────────────────────────
趋势综合     ─┐                               ┌─ Trend Quality
量价配合     ─┤  all momentum                  │    ├─ trend_score (保留)
行业相对强度 ─┤  (r ≈ 0.3-0.5 pairwise)       │    └─ volume_price_score (保留)
价格残差     ─┤                               │
资金持续性   ─┘                               ├─ Relative Strength
超买风险     ──   phantom (never fires)       │    ├─ industry_relative_score (保留, 去1日)
                                              │    └─ capital_score (保留)
                                              │
                                              ├─ Risk / Pricing
                                              │    ├─ risk_score (NEW: 连续型, 替代overbought)
                                              │    └─ price_residual_score (REWRITTEN: 真残差)
                                              │
                                              └─ Fundamental Anchor
                                                   └─ valuation_score (NEW)
```

**Rationale**: The family structure enforces signal diversity by design — each family answers a different question (Is it trending? Is it leading? Is it risky? Is it cheap?). This prevents future drift toward momentum-clustering. Within each family, sub-dimensions are still summed, but cross-family weights can be tuned independently.

### Decision 2: Risk Score — continuous composite replaces binary gate

**Current**: RSI6>74 AND KDJ-J>100 both required → score = 0 or negative. Fires on <1% of samples.

**New**: `risk_score` is a continuous 0-10 scale computed from 3 z-score-based components:
- RSI6 z-score relative to stock's own 60-day distribution (0-4 pts)
- Bollinger Band %B position (0-3 pts)
- Short-term reversal probability: 5-day gain > 2σ → elevated pullback risk (0-3 pts)

All components are normalized within the cross-section each day, so the risk signal is adaptive to market conditions. Higher score = healthier (less overbought).

**Alternatives considered**:
- Keeping the binary gate but lowering thresholds: Still suffers from threshold arbitrariness. Rejected.
- Removing risk entirely: Lost the only negative signal in the model. Rejected.
- Using a single indicator (just RSI): Too noisy, prone to false positives on strong trend days.

### Decision 3: Price Residual — rewritten as true residual

**Current**: 3 sub-scores: MA20乖离率 (valid) + 当日绝对涨幅 (momentum clone) + 尾盘拉升验证. Total max = 15/18.

**New**: 3 sub-scores:
- MA20乖离率 倒U型 (retained, 0-6 pts) — rewards moderate deviation, penalizes extreme
- **残差收益** (0-6 pts): OLS residual of daily return regressed on market return + industry return. Positive residual = stock outperformed what market/industry would predict → alpha signal. This IS the true "price residual."
- 尾盘验证 (retained, 0-3 pts)

The key change: replacing `当日绝对涨幅 → 得高分` with `残差收益 = 实际收益 - E[收益|市场,行业]`.

**Implementation note**: The residual is computed via rolling 20-day OLS of `stock_return ~ market_return + industry_avg_return` per stock. The tushare `daily` table provides the needed return series. For batch computation, market return = 000001.SH daily pct_chg; industry return = mean pct_chg of all candidates in that industry on that day.

### Decision 4: Valuation Anchor — PE/PB percentile

**Current**: PE > 200 is flagged as a warning. No scoring component uses valuation.

**New**: `valuation_score` (0-10 pts) computed as:
- PE(TTM) percentile within same industry (lower = better, 0-5 pts)
- PB percentile within same industry (lower = better, 0-3 pts)
- Dividend yield (if available, 0-2 pts): >2% = 2pt, >1% = 1pt

Industry peers come from the same candidate pool (3 per industry). This is a weak but independent signal — cheap stocks aren't always the best, but extreme overvaluation is a headwind.

**Data source**: `stk_factor_pro` already returns `pe_ttm` and `pb`. Dividend yield available from `daily_basic`.

**Alternatives considered**:
- Absolute PE thresholds: Industry-relative is more appropriate for cross-industry comparison (tech vs. banking).
- PEG or EV/EBITDA: Requires forward earnings data not available in tushare free tier.

### Decision 5: Reversal Signal — contrarian dimension

**New**: `reversal_score` (0-10 pts) captures oversold quality stocks with capitulation characteristics:
- **Capitulation volume** (0-4 pts): 5-day volume acceleration (current vol / 20d avg vol > 2.0) AND 5-day drawdown > 5%
- **Mean-reversion distance** (0-4 pts): How many standard deviations below 20-day MA the price is (Bollinger Band lower penetration)
- **Quality filter** (0-2 pts): Only fires if the stock passes a minimal quality check — market cap > industry median AND non-negative 20-day return (excludes crashing junk stocks)

This dimension naturally has NEGATIVE correlation with the momentum dimensions, creating genuine signal diversity.

**Implementation note**: To avoid catching falling knives, the quality filter is critical. Stocks with market_cap < 50th percentile in their industry or with -20%+ 20-day returns are excluded (reversal_score = 0).

### Decision 6: Time-aware candidate selection

**Current**: `_get_industry_candidates()` reads `stock_pool.db` (current snapshot).

**New**: `_get_industry_candidates_historical(date)` method that:
1. Reads the full symbol list from `stock_pool` (for industry mapping, which is stable)
2. For each date, fetches `daily` table for market cap data (using `total_mv` or estimating from `close * total_share`)
3. Groups by industry, sorts by market cap on that date, takes top 3

For real-time mode, the current `stock_pool` approach is correct (we want today's top-3). For historical mode only, switch to the time-aware method.

**Fallback**: If daily market cap data is unavailable for a date, fall back to current `stock_pool` data and mark `survivorship_bias: true` in debugging output.

### Decision 7: Expanded cross-section for validation

**Current**: `dump_scores.py --limit 30` means each date has exactly 30 stocks → Spearman SE ≈ 0.19.

**New**: Default `--limit` changed to 0 (all candidates). `dump_scores.py` writes all passing candidates per date. The `optimize_weights.py` and `compare_linear_vs_xgb.py` scripts already handle variable cross-sections per date, so no changes needed there.

## Risks / Trade-offs

- **[Risk] New dimensions also show low IC**: Reversal and valuation may also be weak signals in A-share market (which is momentum-driven). Mitigation: The family structure makes it easy to A/B test individual dimensions; if valuation doesn't work, we drop it without restructuring everything.
- **[Risk] PE/PB data quality in tushare**: `stk_factor_pro` `pe_ttm` field is sometimes 0 or NaN. Mitigation: Use `daily_basic` as fallback, and impute missing values with industry median.
- **[Trade-off] 7 dimensions vs 6**: Slightly more complex to tune. The family structure keeps it manageable (4 families × 1-2 dimensions each).
- **[Trade-off] Reversal signal may be anti-correlated with momentum**: By design — this is the point. The composite score will reflect a tension between trend-following and mean-reversion, which is more realistic than pure momentum.
- **[Risk] Historical candidate selection slower**: Requires additional `daily` API calls per date. Mitigation: `dump_scores.py` already runs sequentially per date; the extra queries add ~2-3 seconds per date, acceptable for a nightly batch job.

## Migration Plan

1. Add new scoring methods to `IndustryLeaderboardService` (side-by-side with old ones)
2. Add new response fields to leaderboard endpoint, deprecate old fields but keep them for one version
3. Update `dump_scores.py` to use time-aware candidate selection and expanded cross-section
4. Run new scoring against historical data → compare IC with baseline via `compare_linear_vs_xgb.py`
5. If new model shows improvement, switch frontend to new fields and remove deprecated ones
6. Update `optimize_weights.py` to handle 7 dimensions (just add to DIMS list)

Rollback: Old scoring methods remain in the service class during migration. Reverting is a one-line flag change.

## Open Questions

- Q: Should reversal_score and valuation_score also be computed in Round 2 (top-N precision) like capital_score, or batch-computed for all candidates in Round 1?
  - Tentative: Both are batch-computable from stk_factor_pro + daily data already fetched in Round 1, so compute for all candidates in Round 1.
- Q: Do we need to re-run the full `dump_scores.py` historical backfill (60 days × all stocks = slower)?
  - Yes, required for validation. Estimate: 60 days × 5s/day ≈ 5 minutes with the new time-aware selection.
