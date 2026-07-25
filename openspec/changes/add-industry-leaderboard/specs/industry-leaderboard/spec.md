## ADDED Requirements

### Requirement: Industry leader selection by market cap
The system SHALL select the top 3 stocks by market capitalization from each Shenwan primary industry as candidates for the leaderboard.

#### Scenario: Normal industry with sufficient stocks
- **WHEN** an industry has 3 or more non-ST, non-delisted stocks with valid market_cap values
- **THEN** the top 3 by market_cap descending SHALL be selected as candidates

#### Scenario: Industry with fewer than 3 stocks
- **WHEN** an industry has fewer than 3 eligible stocks
- **THEN** all eligible stocks from that industry SHALL be included

#### Scenario: ST and delisted stocks excluded
- **WHEN** a stock is marked as ST (is_st=1) or has been delisted
- **THEN** it SHALL NOT appear in candidate selection

### Requirement: Hard filters before scoring
The system SHALL apply hard filters to exclude or flag unsuitable candidates before computing scores.

#### Scenario: Low turnover exclusion
- **WHEN** a candidate's daily turnover (成交额) is below 1 billion CNY
- **THEN** it SHALL be excluded from the leaderboard

#### Scenario: Limit-up one-sided board
- **WHEN** a candidate's open price equals high price equals close price AND change_pct > 9.5%
- **THEN** it SHALL be marked as "untradeable" (不可交易) but still displayed

#### Scenario: High PE flagging
- **WHEN** a candidate's PE(TTM) exceeds 200
- **THEN** it SHALL be marked with "高估值风险" warning

### Requirement: Market regime detection
The system SHALL detect the current market regime (trending vs ranging) and adjust scoring weights accordingly.

#### Scenario: Trending market
- **WHEN** the market index (000001.SH) ADX > 25 AND MA5 > MA20
- **THEN** the trending weight scheme SHALL be applied (Trend 28%, Volume-Price 15%, Industry Relative 17%, Price Residual 15%, Capital 25%)

#### Scenario: Ranging market
- **WHEN** the market index ADX < 20
- **THEN** the ranging weight scheme SHALL be applied (Trend 22%, Volume-Price 18%, Industry Relative 20%, Price Residual 18%, Capital 22%)
- **AND** if ADX < 15, trend composite score SHALL be multiplied by 0.6; if ADX 15-20, multiplied by 0.8

#### Scenario: Transitional regime
- **WHEN** the market index ADX is between 20 and 25
- **THEN** weights SHALL be the average of trending and ranging schemes

### Requirement: Two-round scoring — Round 1 (4 dimensions, all candidates)
The system SHALL compute scores for all ~330 candidates using four dimensions in Round 1, with capital dimension set to a neutral placeholder.

#### Scenario: Round 1 — all candidates scored
- **WHEN** the leaderboard endpoint is called
- **THEN** all candidates SHALL be scored on Trend Composite, Volume-Price, Industry Relative Strength, and Price Residual
- **AND** capital persistence SHALL be set to 50% of its maximum score as a neutral placeholder

#### Scenario: Round 1 — Top 10 identified
- **WHEN** Round 1 scoring completes
- **THEN** the top 10 candidates by composite score SHALL be identified for Round 2 capital scoring

### Requirement: Trend Composite scoring
The system SHALL compute the Trend Composite dimension score based on MA alignment, MACD histogram strength, and ADX trend intensity.

#### Scenario: Trend Composite — MA alignment layers
- **WHEN** a stock's MA5 > MA10 > MA20 > MA60 (full bullish alignment)
- **THEN** trend MA sub-score SHALL be 10 (trending) or 8 (ranging)
- **WHEN** MA5 is below MA20 (death cross)
- **THEN** trend MA sub-score SHALL be 0-2

#### Scenario: Trend Composite — MACD histogram strength
- **WHEN** MACD DIF is above DEA AND MACD histogram is expanding
- **THEN** MACD sub-score SHALL be 8-10 (trending) or 6-7 (ranging)
- **WHEN** MACD DIF is below DEA
- **THEN** MACD sub-score SHALL be 0-3

#### Scenario: Trend Composite — ADX trend strength
- **WHEN** ADX > 40 (strong trend)
- **THEN** ADX sub-score SHALL be 8 (trending) or 7 (ranging)
- **WHEN** ADX is between 25 and 40
- **THEN** ADX sub-score SHALL be linearly scaled between 3-8

#### Scenario: Trend Composite — trend initiation bonus
- **WHEN** ADX > 25 AND MACD golden cross occurred within last 5 trading days
- **THEN** a +2 trend initiation bonus SHALL be added (trending market only)

### Requirement: Volume-Price coordination scoring
The system SHALL compute volume-price coordination using historical daily bar data with volume analysis.

#### Scenario: Volume-Price — trend alignment
- **WHEN** the ratio of average up-day volume to average down-day volume over the last 10 days > 1.5
- **THEN** volume-price match sub-score SHALL be 7 (trending) or 8 (ranging)
- **WHEN** the ratio < 0.8
- **THEN** volume-price match sub-score SHALL be 0-2

#### Scenario: Volume-Price — breakout volume ratio
- **WHEN** a stock breaks through a key moving average AND the day's volume > 2.0 × 20-day average volume
- **THEN** breakout volume sub-score SHALL be 5 (trending) or 6 (ranging)

#### Scenario: Volume-Price — pullback shrinkage health
- **WHEN** a stock pulls back AND the day's volume < 0.7 × 20-day average volume
- **THEN** pullback health sub-score SHALL be 3 (trending) or 4 (ranging)
- **WHEN** pullback volume > 1.2 × average
- **THEN** pullback health sub-score SHALL be 0

### Requirement: Industry Relative Strength scoring
The system SHALL compute industry-relative strength by comparing a candidate against its same-industry peers.

#### Scenario: Industry Relative Strength — 1-day excess return
- **WHEN** a stock's daily change_pct exceeds the same-industry candidate average by > 2%
- **THEN** 1-day excess return sub-score SHALL be 7
- **WHEN** the stock underperforms the industry average
- **THEN** sub-score SHALL be 0-2

#### Scenario: Industry Relative Strength — 5-day cumulative excess return
- **WHEN** 5-day cumulative excess return > 5%
- **THEN** 5-day excess return sub-score SHALL be 6 (trending) or 7 (ranging)

#### Scenario: Industry Relative Strength — turnover contribution
- **WHEN** a stock's turnover accounts for > 50% of the total turnover of all candidates in its industry
- **THEN** turnover contribution sub-score SHALL be 4 (trending) or 6 (ranging)

### Requirement: Price Residual scoring
The system SHALL compute price residual as a risk indicator, replacing absolute daily change with relative excess change.

#### Scenario: Price Residual — MA20 deviation (inverted-U)
- **WHEN** price deviation from MA20 is 3-10% (trending) or 2-8% (ranging)
- **THEN** deviation sub-score SHALL be 6 (trending) or 8 (ranging)
- **WHEN** deviation > 15%
- **THEN** deviation sub-score SHALL approach 0

#### Scenario: Price Residual — relative industry excess change
- **WHEN** a stock's daily change_pct exceeds the industry candidate average by > 2%
- **THEN** excess change sub-score SHALL be 6 (trending) or 7 (ranging)

#### Scenario: Price Residual — non-tail-session pump verification
- **WHEN** less than 30% of the day's total gain occurred after 14:55
- **THEN** verification sub-score SHALL be 3
- **WHEN** more than 70% of gain occurred after 14:55
- **THEN** verification sub-score SHALL be 0

### Requirement: Two-round scoring — Round 2 (Capital Persistence, Top 10 only)
After Round 1 identifies the top 10 candidates, the system SHALL fetch real-time capital flow data for these 10 stocks and recompute their rankings.

#### Scenario: Round 2 — fetch real-time moneyflow for top 10
- **WHEN** Round 1 top 10 are identified
- **THEN** the system SHALL fetch real-time main_net, main_pct, and d5_main_net for each of the top 10 via the East Money real-time interface
- **AND** requests SHALL be made serially (one-by-one, to avoid triggering anti-scraping blocks)

#### Scenario: Round 2 — capital persistence scoring
- **WHEN** real-time moneyflow data is available for a top-10 candidate
- **THEN** capital persistence score SHALL be computed from: daily main_net / float_market_cap (0-10/8) + main_pct (0-8/7) + 5-day cumulative main_net / float_market_cap (0-7/7)
- **AND** the top 10 SHALL be re-ranked by updated composite score

#### Scenario: Round 2 — moneyflow unavailable
- **WHEN** the East Money interface is unavailable for a top-10 candidate
- **THEN** the capital dimension SHALL remain at the neutral placeholder (50% of max)
- **AND** the response SHALL include `capital_data: "unavailable"` for that candidate

#### Scenario: Non-top-10 capital score
- **WHEN** a candidate is ranked 11 or below
- **THEN** capital persistence score SHALL remain at 50% of maximum (neutral placeholder)

### Requirement: Penalty coefficients
The system SHALL apply penalty coefficients after scoring.

#### Scenario: Dimension floor penalty
- **WHEN** any single dimension score is below 20% of its maximum possible score
- **THEN** the total composite score SHALL be multiplied by 0.7

#### Scenario: Overheat warning
- **WHEN** RSI6 > 90 AND MA20 deviation > 15%
- **THEN** the candidate SHALL be flagged with "过热预警" AND price residual dimension SHALL lose 3 points

### Requirement: Batch data fetching for Round 1
The system SHALL fetch all Round 1 data in at most 3 external API calls regardless of candidate count.

#### Scenario: Real-time price batch fetch
- **WHEN** the leaderboard endpoint is called
- **THEN** the system SHALL fetch current price, change_pct, turnover_rate, and turnover_amount for all candidates in a single Tencent qt.gtimg.cn batch request

#### Scenario: Technical indicators batch fetch
- **WHEN** the leaderboard endpoint is called
- **THEN** the system SHALL fetch MA5/10/20/60, MACD DIF/DEA/histogram, ADX, PDI, MDI, RSI6, close, PE_TTM, volume, amount from Tushare stk_factor_pro in a single batch call

#### Scenario: Historical daily bars batch fetch
- **WHEN** the leaderboard endpoint is called
- **THEN** the system SHALL fetch the last 20 trading days of OHLCV data from Tushare daily in a single batch call

#### Scenario: Tencent quote unavailable — Tushare fallback
- **WHEN** the Tencent real-time quote fetch fails or times out (5 seconds)
- **THEN** the system SHALL fall back to Tushare daily table for today's OHLCV data
- **AND** mark `data_source: "tushare"` in the response

#### Scenario: Daily batch timeout — volume degradation
- **WHEN** the Tushare daily batch call times out
- **THEN** the volume-price dimension SHALL be computed using only the current day's volume_ratio from stk_factor_pro
- **AND** mark `volume_data: "degraded"` in the response

### Requirement: Leaderboard API endpoint
The system SHALL expose a REST endpoint that returns industry leaderboard rankings with scores.

#### Scenario: Default ranking
- **WHEN** GET /api/v1/market/industry-leaderboard is called without parameters
- **THEN** response SHALL return up to 50 ranked items sorted by composite_score descending
- **AND** each item SHALL include symbol, name, industry, market_cap, change_pct, turnover_rate, composite_score, market_regime, all five dimension sub-scores, and capital_data status

#### Scenario: Filtered by industry
- **WHEN** GET /api/v1/market/industry-leaderboard?industry=电子 is called
- **THEN** response SHALL return only candidates from the specified industry

#### Scenario: Custom sort
- **WHEN** GET /api/v1/market/industry-leaderboard?sort_by=trend_score is called
- **THEN** response SHALL return items sorted by the specified score dimension descending

#### Scenario: Custom limit
- **WHEN** GET /api/v1/market/industry-leaderboard?limit=10 is called
- **THEN** response SHALL return at most 10 items

#### Scenario: Market regime in response
- **WHEN** the leaderboard endpoint is called
- **THEN** the response SHALL include `market_regime` field with value "trending", "ranging", or "transitional"

### Requirement: Server-side caching
The system SHALL cache leaderboard results for 60 seconds.

#### Scenario: Cache hit
- **WHEN** the leaderboard endpoint is called within 60 seconds of a previous call
- **AND** no force-refresh parameter is set
- **THEN** the cached result SHALL be returned without re-fetching external data

#### Scenario: Force refresh
- **WHEN** GET /api/v1/market/industry-leaderboard?refresh=true is called
- **THEN** the cache SHALL be bypassed and fresh data SHALL be fetched (including Round 2 capital data for the new top 10)

### Requirement: Frontend leaderboard page
The system SHALL provide a React page displaying the leaderboard with 5-minute auto-refresh.

#### Scenario: Page load
- **WHEN** the leaderboard page is navigated to
- **THEN** the top 20 ranked stocks SHALL be displayed in a table with rank, name, industry, change_pct, composite_score, and five dimension sub-scores

#### Scenario: Auto-refresh
- **WHEN** the leaderboard page is open
- **THEN** data SHALL automatically refresh every 5 minutes

#### Scenario: Industry filter
- **WHEN** the user selects an industry from the dropdown filter
- **THEN** only stocks from that industry SHALL be displayed

#### Scenario: Sort toggle
- **WHEN** the user clicks a column header
- **THEN** the table SHALL re-sort by that column descending

#### Scenario: Market regime display
- **WHEN** the leaderboard page is displayed
- **THEN** the current market regime (趋势市/震荡市/过渡期) SHALL be shown prominently at the top
