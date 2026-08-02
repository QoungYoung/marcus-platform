## ADDED Requirements

### Requirement: Direction prediction with lagged features
The system SHALL predict expected holding-period return for industry leader stocks using features lagged by 1 trading day. All stock-level features MUST be computed from T-1 and earlier data only. The prediction target SHALL be `(T+N_close - T_open) / T_open × 100` for each horizon N ∈ {1, 3, 5}.

#### Scenario: Feature lag compliance
- **WHEN** the model runs at 15:01 on trading day T
- **THEN** all individual stock features (change_pct, turnover_rate, vol_ratio_1d, rsi6, rsi14, ma20_deviation, ret_5d, ret_10d, consecutive_up, consecutive_down, gap_up_pct, big_order_net, main_force_ratio, flow_5d_cum) are derived from T-1 and earlier data only
- **THEN** the model outputs expected_return for each horizon, representing (T+N close - T open) / T open

#### Scenario: Same-day open price available
- **WHEN** the model runs at 9:26 after market auction on execution day T
- **THEN** T_open price is available and used as the execution reference price
- **THEN** expected_return already accounts for overnight gap from T-1_close to T_open

### Requirement: Cross-sectional feature set (26 dimensions)
The model SHALL use exactly 26 features: 19 individual stock features + 7 sector features. Market-wide features (MARKET_COLS) and index features (INDEX_COLS) SHALL NOT be included in the feature matrix. Market timing SHALL be handled by an independent module outside the XGBoost model.

#### Scenario: Feature matrix construction
- **WHEN** building the feature DataFrame for prediction
- **THEN** the column set is exactly FEATURE_COLS + SECTOR_COLS (26 columns)
- **THEN** MARKET_COLS and INDEX_COLS are absent from model input

### Requirement: Sector-level features with full-market computation
The system SHALL compute 7 sector features per stock: sector_pct, sector_ret_5d, sector_vol, sector_mf (individual deviation from industry median), and sector_money_flow, sector_breadth, sector_rank (industry-wide aggregates same for all stocks in an industry). Industry medians SHALL be computed from ALL active stocks (not just top 3 leaders) to ensure statistical validity.

#### Scenario: Sector feature computation
- **WHEN** computing sector features for a trading day
- **THEN** industry medians are calculated from all stocks with valid data, not just the top 3 leaders
- **THEN** sector_rank is the percentile rank of the industry's median change_pct among all industries

### Requirement: Holding-period return regression target
The training target SHALL be the holding-period return `(T+N_close - T_open) / T_open × 100`, winsorized per-board (main board and ChiNext/STAR board independently at 1%/99% quantiles). The model SHALL use Pseudo-Huber loss to robustly handle extreme returns without artificially clipping legitimate volatility.

#### Scenario: Target computation
- **WHEN** computing target_5d for a main board stock with T_open=100 and T+5_close=108
- **THEN** target_5d = 8.0 (not winsorized since +8% is within main board ±10% normal range)

#### Scenario: ChiNext stock target preservation
- **WHEN** computing target_1d for a ChiNext stock with a +15% return
- **THEN** the return is NOT clipped by main board 1%/99% quantiles
- **THEN** the return is only clipped if it exceeds ChiNext board's own 1%/99% quantiles

### Requirement: Adjusted prices for all computations
All price data (open, close, high, low) SHALL be multiplied by adj_factor before feature computation and target calculation to ensure forward-adjusted consistency.

#### Scenario: Dividend-adjusted prices
- **WHEN** a stock has adj_factor = 1.05 due to a historical dividend
- **THEN** open and close prices used in all computations are multiplied by 1.05

### Requirement: Training data limited to industry leaders
The training data pipeline SHALL output only the top 3 stocks by total market value (total_mv) per industry per trading day. Industry statistics (median, sum, breadth) SHALL be computed from the full active symbol set before filtering.

#### Scenario: Leader filtering
- **WHEN** generating training data for a trading day with 5000 active stocks across 110 industries
- **THEN** approximately 330 rows (110 × 3) are written to the output CSV
- **THEN** sector features are computed before filtering for correct industry medians

### Requirement: Model evaluation on long-only basis
Walk-forward validation SHALL report long-only portfolio metrics: Information Ratio vs CSI 300, monthly win rate, maximum drawdown, and Calmar ratio. Long-Short spread SHALL NOT be used as a primary evaluation metric. Net returns SHALL deduct 0.18% transaction cost per round-trip trade.

#### Scenario: Walk-forward evaluation report
- **WHEN** a walk-forward training run completes for 38 windows
- **THEN** the report shows annualized excess return, IR, monthly win rate, max drawdown, and Calmar ratio
- **THEN** all return figures are net of 0.18% transaction cost
