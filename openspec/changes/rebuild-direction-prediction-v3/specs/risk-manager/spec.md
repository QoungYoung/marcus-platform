## ADDED Requirements

### Requirement: Dynamic position scaling based on drawdown
The system SHALL adjust position size continuously based on real-time net asset value (NAV) drawdown from peak. The scale range SHALL be 0.2 (minimum) to 1.0 (maximum), never zero (no full liquidation). Scaling SHALL be linear when drawdown is between 2% and 5%, and accelerated when drawdown is between 5% and the maximum limit (8%).

#### Scenario: Normal market (drawdown < 2%)
- **WHEN** current drawdown from peak NAV is 1.5%
- **THEN** position scale is 1.0 (full position)

#### Scenario: Moderate drawdown (2% to 5%)
- **WHEN** current drawdown from peak NAV is 3.5%
- **THEN** position scale is linear between 1.0 and 0.6 based on drawdown percentage

#### Scenario: Severe drawdown (5% to 8%)
- **WHEN** current drawdown from peak NAV is 7%
- **THEN** position scale is accelerated between 0.6 and 0.3

#### Scenario: Maximum drawdown exceeded
- **WHEN** current drawdown from peak NAV exceeds 8%
- **THEN** position scale is 0.2 (minimum, never zero)

### Requirement: NAV tracking
The system SHALL maintain a daily NAV history and track the peak NAV. The update SHALL be called once per trading day after market close.

#### Scenario: NAV update after profitable day
- **WHEN** the daily portfolio PnL is +1.2% and previous NAV was 1.05
- **THEN** new NAV is 1.05 × 1.012 = 1.0626
- **THEN** peak NAV is updated to 1.0626

#### Scenario: NAV update after losing day
- **WHEN** the daily portfolio PnL is -2.0% and previous NAV was 1.0626
- **THEN** new NAV is 1.0626 × 0.98 = 1.0413
- **THEN** peak NAV remains at 1.0626
- **THEN** drawdown is (1.0626 - 1.0413) / 1.0626 = 2.0%

### Requirement: Emergency retrain signal
The system SHALL monitor the Top-N prediction performance daily. If the mean actual return of the top 10 predicted stocks is negative AND underperforms the benchmark for 3 consecutive trading days, the system SHALL signal an emergency retrain.

#### Scenario: Emergency retrain triggered
- **WHEN** for 3 consecutive days, mean(Top-10 actual return) < 0 AND mean(Top-10 actual return) < benchmark return
- **THEN** emergency_check() returns True, triggering immediate model retraining

#### Scenario: No emergency
- **WHEN** 2 out of 3 days are underperforming but the 3rd day is positive
- **THEN** emergency_check() returns False
