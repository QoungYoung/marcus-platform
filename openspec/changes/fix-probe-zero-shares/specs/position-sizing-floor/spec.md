## ADDED Requirements

### Requirement: Probe/rec shares floor to 100 when max can buy
The system SHALL set `probe_shares` and `rec_shares` to 100 when their independently-capped calculation yields 0 shares, but `max_shares >= 100`.

#### Scenario: Expensive stock where probe cap is too small
- **WHEN** `probe_amount_raw = min(10% × total_asset, max_usable)` results in fewer than 100 shares after `_round_lot`
- **AND** `max_shares >= 100`
- **THEN** `probe_shares` SHALL be set to 100, `probe_amount` = 100 × current_price
- **AND** a warning SHALL be appended noting that the probe position exceeds the intended percentage

#### Scenario: Rec shares floor when role cap too small
- **WHEN** `rec_amount_raw = min(role_cap_pct% × total_asset, max_usable)` yields 0 shares after `_round_lot`
- **AND** `max_shares >= 100`
- **THEN** `rec_shares` SHALL be set to 100

#### Scenario: max_shares is also 0 — no floor applied
- **WHEN** `max_shares == 0`
- **THEN** neither probe nor rec SHALL be floored to 100
- **AND** `all_pass` SHALL be `false`

### Requirement: all_pass must be false when position cannot be built
The system SHALL set `all_pass` to `false` when the calculated `max_shares` is 0, regardless of individual validation checks passing.

#### Scenario: Account too small for minimum lot
- **WHEN** `max_shares == 0` after all constraints and floor logic
- **THEN** `all_pass` SHALL be `false`
- **AND** warnings SHALL include a reason identifying which constraint blocked the position

#### Scenario: Position can be built — normal behavior
- **WHEN** `max_shares >= 100`
- **AND** all validation checks pass
- **THEN** `all_pass` SHALL be `true`

### Requirement: calculate_position_quantity parity
The shared `calculate_position_quantity` function SHALL apply the same floor logic: when its computed `max_shares` is 0, the returned `shares` SHALL be 0 and `warnings` SHALL include the blocking reason.

#### Scenario: Backtest sandbox calls calculate_position_quantity with insufficient funds
- **WHEN** `calculate_position_quantity` is called with parameters that result in fewer than 100 shares after `_round_lot`
- **THEN** `shares` SHALL be 0
- **AND** warnings SHALL describe which constraint (single cap, total cap, or cash reserve) limited the position
