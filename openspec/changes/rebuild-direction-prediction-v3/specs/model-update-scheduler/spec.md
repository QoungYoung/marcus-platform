## ADDED Requirements

### Requirement: Monthly scheduled retraining
The system SHALL retrain the direction prediction model on the last trading day of each calendar month. The retraining SHALL use the most recent 250 trading days of data with a fixed 120-day rolling walk-forward window. Previously tuned Optuna hyperparameters SHALL be reused without re-searching.

#### Scenario: Monthly retrain trigger
- **WHEN** today is the last trading day of the month and it is after 16:00
- **THEN** should_retrain() returns True
- **THEN** the model is retrained with data from the last 250 trading days

#### Scenario: Non-retrain day
- **WHEN** today is the 15th of the month (not month-end)
- **THEN** should_retrain() returns False

### Requirement: Emergency retraining on consecutive underperformance
The system SHALL trigger an immediate retraining when the RiskManager signals 3 consecutive days of Top-10 underperformance. Emergency retraining SHALL use only the most recent 60 trading days with a fixed 60-day rolling window and SHALL reuse existing hyperparameters. Training time SHALL be under 1 minute.

#### Scenario: Emergency retrain triggered
- **WHEN** RiskManager.emergency_check() returns True
- **THEN** the model is immediately retrained with 60-day data, reusing cached Optuna params
- **THEN** the retraining completes in under 60 seconds

#### Scenario: Post-emergency schedule reset
- **WHEN** an emergency retrain is triggered on the 20th of the month
- **THEN** the next scheduled monthly retrain still occurs at month-end as normal

### Requirement: Retraining frequency limits
The system SHALL limit emergency retrains to at most once per 5 trading days to prevent thrashing during volatile markets. Monthly scheduled retrains are unaffected by this limit.

#### Scenario: Emergency retrain rate limit
- **WHEN** an emergency retrain completed 3 trading days ago and another emergency is signaled
- **THEN** the emergency retrain is suppressed until 5 trading days have elapsed since the last one
