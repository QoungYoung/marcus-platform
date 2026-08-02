## ADDED Requirements

### Requirement: Iron Rule 2 uses session peak float for tier determination
The Iron Rule 2 trailing stop system SHALL use the highest float P&L percentage achieved during the current trading session (session max float) to determine the protection tier, rather than the current float P&L percentage.

#### Scenario: Peak float reaches T3, current float drops below T1
- **WHEN** a position achieved float P&L of +12% earlier in the session (reaching T3 tier for high-volatility stocks)
- **AND** the current float P&L has dropped to +2% (below T1 threshold)
- **THEN** the protection tier SHALL still be T3 (based on session peak +12%)
- **AND** the protection line SHALL remain at T3's protect_pct (+5% for high-volatility stocks)
- **AND** the system SHALL trigger a stop-loss when current float drops below the T3 protection line

#### Scenario: Peak float only reaches T1
- **WHEN** the session max float reached +3% (T1 tier for high-volatility stocks)
- **AND** has never reached T1.5, T2, or T3 during this session
- **AND** the current float P&L drops to -1%
- **THEN** the protection tier SHALL be T1 (based on session peak +3%)
- **AND** the protection line SHALL be 0% (cost price, subject to holding days ≥ 3 check)
- **AND** the system SHALL trigger Iron Rule 2 when float drops below cost price

#### Scenario: Session max float resets daily
- **WHEN** a new trading day begins
- **THEN** the session max float for all symbols SHALL be reset
- **AND** tier determination on the new day SHALL use only that day's peak float

#### Scenario: Session max float monotonically increases during the day
- **WHEN** the current float P&L is higher than the stored session max float
- **THEN** the session max float SHALL be updated to the current value
- **WHEN** the current float P&L is lower than the stored session max float
- **THEN** the session max float SHALL remain unchanged (preserving the peak)

#### Scenario: No session max float for newly opened positions
- **WHEN** a position was opened today (T+1 locked shares excluded from check)
- **AND** the available shares count is zero
- **THEN** no Iron Rule 2 evaluation SHALL be performed for this position

### Requirement: Session max float tracking
The system SHALL maintain a per-symbol session max float dictionary that tracks the highest float P&L percentage observed during the current trading session.

#### Scenario: Session max float initialization
- **WHEN** the first check cycle of the day evaluates a position
- **THEN** the session max float SHALL be initialized to the current float P&L percentage

#### Scenario: Session max float cleanup on daily reset
- **WHEN** `_daily_reset()` is called at the start of a new trading day
- **THEN** the session max float dictionary SHALL be emptied
