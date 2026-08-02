## ADDED Requirements

### Requirement: Binary direction classifier per horizon
The system SHALL provide three independent binary classifiers predicting P(return > 0) for 1-day, 3-day, and 5-day forward horizons. Each classifier SHALL output a probability score in [0, 1] for every candidate stock.

#### Scenario: Daily prediction at market close
- **WHEN** the market closes and daily OHLCV data is available
- **THEN** the system generates up-probability scores for all candidate stocks at 1d/3d/5d horizons

#### Scenario: Historical backtest
- **WHEN** a historical date is provided as input
- **THEN** the system generates predictions using only data available on or before that date (no lookahead)

### Requirement: Walk-forward validation
The system SHALL be validated using expanding-window walk-forward cross-validation with train=30 trading days and test=3 trading days step. Each test window SHALL report accuracy, precision, recall, and ROC-AUC vs the always-guess-majority baseline.

#### Scenario: Validation run
- **WHEN** validation is executed on historical data with at least 45 trading days of coverage
- **THEN** the system outputs per-window metrics and aggregate mean accuracy across all test windows

### Requirement: Classifier accuracy target
The XGBoost classifier SHALL achieve walk-forward accuracy exceeding the always-guess-majority baseline by at least 5 percentage points on average across test windows.

#### Scenario: Success criterion met
- **WHEN** walk-forward validation completes on 10+ test windows
- **THEN** mean classifier accuracy exceeds baseline accuracy by 5% or more

#### Scenario: Success criterion not met
- **WHEN** walk-forward accuracy improvement is less than 5% over baseline
- **THEN** the system falls back to the market regime classifier output only, with stock-level features disabled

### Requirement: Probability calibration
The classifier SHALL output well-calibrated probabilities, where P=0.6 means approximately 60% of predictions with that score are correct. Calibration SHALL use isotonic regression on a held-out validation set.

#### Scenario: Calibrated output
- **WHEN** the classifier outputs P=0.65 for a stock
- **THEN** approximately 65% of stocks with similar scores actually have positive forward returns
