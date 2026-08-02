## ADDED Requirements

### Requirement: Money flow features
The system SHALL compute money flow features from Tushare `moneyflow` API: big_order_net_flow (超大单+大单净流入), small_order_net_flow (小单净流入), main_force_ratio (主力净流入/成交额), and 5-day cumulative net flow. These features SHALL be normalized within the daily cross-section.

#### Scenario: Money flow data available
- **WHEN** Tushare `moneyflow` returns valid data for a stock on the target date
- **THEN** the system computes big_order_net_flow, main_force_ratio, and 5d cumulative flow

#### Scenario: Money flow data unavailable
- **WHEN** Tushare `moneyflow` returns no data or fails
- **THEN** the system falls back to volume-based approximation: amount × close_pct_change as a proxy for net flow direction

### Requirement: Volume breakout features
The system SHALL compute volume breakout ratios: vol_ratio_5d (5-day avg volume / 20-day avg volume), vol_ratio_1d (today's volume / 20-day avg volume), and amount_breakout (today's amount > 2× 20-day median amount). These features SHALL use Tushare `daily` OHLCV data.

#### Scenario: Normal volume day
- **WHEN** today's volume is within 0.5-1.5× of 20-day average
- **THEN** vol_ratio_5d is near 1.0 and does not trigger breakout flags

#### Scenario: Volume spike day
- **WHEN** today's volume exceeds 2.0× of 20-day average
- **THEN** vol_ratio_1d > 2.0 and amount_breakout flag is set

### Requirement: Streak and gap features
The system SHALL compute consecutive up/down day counts and gap detection features from daily OHLCV data: consecutive_up_days, consecutive_down_days, gap_up_pct (today's open / yesterday's close - 1), gap_fill_pct (today's close relative to gap).

#### Scenario: Multi-day winning streak
- **WHEN** a stock has closed higher for 5 consecutive trading days
- **THEN** consecutive_up_days = 5 and the streak feature contributes positively to short-term direction probability

#### Scenario: Gap down opening
- **WHEN** today's open is 3% below yesterday's close
- **THEN** gap_down_pct = -3% and gap_fill_pct tracks whether price recovers during the session

### Requirement: Market breadth features
The system SHALL compute daily cross-sectional market breadth features: up_ratio (fraction of candidate stocks with positive return), limit_up_count, advance_decline_ratio (count of advancing / count of declining), and market_volume_trend (total market volume vs 5-day average). These features are shared across all stocks on the same date.

#### Scenario: Broad rally day
- **WHEN** 70% of candidate stocks have positive daily returns AND 5+ stocks hit limit-up
- **THEN** up_ratio > 0.7 and limit_up_count >= 5, both features signal favorable market regime
