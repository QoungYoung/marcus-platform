# Entry Filter Pattern & Divergence

## Purpose

Defines the K-line candlestick pattern recognition and volume-price divergence detection added to Layer 3 (overbought/entry filter) of `check_entry_filters`. Identifies bearish reversal patterns (shooting star, bearish engulfing) from daily OHLC data and detects volume-price divergence when price makes new highs without volume confirmation.

## Requirements

### Requirement: Shooting star detection
The system SHALL detect the shooting star (射击之星) candlestick pattern from daily OHLC data using the following criteria:

- Real body = |close - open|
- Upper shadow = high - max(open, close)
- Lower shadow = min(open, close) - low
- Upper shadow ≥ 2.0 × real body
- Upper shadow > lower shadow × 1.5 (upper shadow dominates)
- Real body > 0 (not a doji)

When a shooting star is detected on the most recent completed trading day, the system SHALL trigger a hard block (no entry, multiplier = 0).

#### Scenario: Shooting star detected
- **WHEN** the last completed daily bar has open=93.93, high=96.80, low=93.35, close=94.30
- **THEN** real body is 0.37, upper shadow is 2.50 (6.76x body), upper shadow > lower shadow × 1.5
- **AND** shooting star is detected, triggering hard block

#### Scenario: No shooting star on normal candle
- **WHEN** the last completed daily bar has open=93.0, high=95.0, low=91.0, close=94.5
- **THEN** real body is 1.50, upper shadow is 0.50 (0.33x body)
- **AND** shooting star is NOT detected

#### Scenario: Shooting star uses completed day only
- **WHEN** checking patterns during intraday trading
- **THEN** the shooting star check SHALL use the most recent COMPLETED daily bar from daily K-line data (not today's incomplete bar)

### Requirement: Bearish engulfing detection
The system SHALL detect the bearish engulfing (看跌吞没) candlestick pattern using the two most recent completed daily bars:

- Bar[-1] (previous day): close > open (bullish candle)
- Bar[0] (last completed day): close < open (bearish candle)
- Bar[0].open > Bar[-1].close
- Bar[0].close < Bar[-1].open

When a bearish engulfing is detected, the system SHALL trigger a hard block (no entry, multiplier = 0).

#### Scenario: Bearish engulfing detected
- **WHEN** Bar[-1] has open=93.0, close=95.0 (bullish) and Bar[0] has open=96.0, close=92.0 (bearish)
- **THEN** Bar[0].open (96.0) > Bar[-1].close (95.0) and Bar[0].close (92.0) < Bar[-1].open (93.0)
- **AND** bearish engulfing is detected, triggering hard block

#### Scenario: No bearish engulfing on continuation pattern
- **WHEN** Bar[-1] is bullish and Bar[0] has open=94.0, close=93.5 (small bearish inside bar)
- **THEN** Bar[0].open (94.0) < Bar[-1].close (95.0), no engulfing
- **AND** bearish engulfing is NOT detected

### Requirement: Volume-price divergence detection
The system SHALL detect volume-price divergence over a 5-day window using the following criteria:

1. The current day's high price is the highest high in the 5-day window
2. The current day's volume is less than the maximum volume in the 5-day window (excluding current day)

When volume-price divergence is detected, the system SHALL trigger a warning (probe position only, multiplier ≤ 0.5).

#### Scenario: Volume-price divergence detected
- **WHEN** today's high is 96.80 (5-day highest) and today's volume is 671,593
- **AND** the 5-day max volume was 687,346 on a day with a lower high (95.95)
- **THEN** price made new high but volume did not confirm → divergence warning
- **AND** downgrade_multiplier set to ≤ 0.5

#### Scenario: No divergence when volume confirms
- **WHEN** today's high is 96.80 (5-day highest) and today's volume is 750,000 (also 5-day highest)
- **THEN** price and volume both made new highs → no divergence
- **AND** volume-price check passes

#### Scenario: No divergence when price not at new high
- **WHEN** today's high is 94.0 and the 5-day highest high is 96.80
- **THEN** price has not made a new high → divergence check not applicable
- **AND** volume-price check passes

### Requirement: Pattern/divergence integration with Layer 3
The system SHALL integrate pattern and divergence results into the Layer 3 overbought filter as follows:

1. Compute overbought severity from RSI6/KDJ-J/CCI (existing logic)
2. If pattern detection triggers hard block → add severity 3 indicator to resonance calculation
3. If divergence detection triggers warning → add severity 1 indicator to resonance calculation
4. Pattern severity 3 SHALL always result in final severity 3 regardless of resonance logic
5. Pattern and divergence findings SHALL be appended to `LayerResult.details`

#### Scenario: Normal overbought but shooting star blocks
- **WHEN** RSI6=72, KDJ-J=80, CCI=123 (all normal severity 0)
- **AND** shooting star pattern detected (hard block, severity 3)
- **THEN** Layer 3 final result is "Hard Block" with multiplier = 0

#### Scenario: Divergence downgrades an otherwise normal pass
- **WHEN** RSI6=70, KDJ-J=85, CCI=110 (all normal severity 0)
- **AND** volume-price divergence detected (warning, severity 1)
- **THEN** Layer 3 passes but downgrade_multiplier ≤ 0.5
- **AND** details include divergence message
