## ADDED Requirements

### Requirement: Exit signal based on greed recovery percentile
The system SHALL detect exit signals for indices that are currently in golden pit or warning status. An exit signal is triggered when the greed value's expanding-window percentile rises above a configured threshold after the turning point has been confirmed.

#### Scenario: Half exit at P30 after turning point
- **WHEN** an index has turning_point_confirmed=True AND its greed percentile rises above P30 for the first time since turning point
- **THEN** the system SHALL emit a "half_exit" signal, indicating 50% of the position should be sold

#### Scenario: Full exit at P50 after turning point
- **WHEN** an index has turning_point_confirmed=True AND its greed percentile rises above P50
- **THEN** the system SHALL emit a "full_exit" signal, indicating the entire position should be sold

#### Scenario: Stop-profit on trend reversal
- **WHEN** an index has turning_point_confirmed=True AND greed has declined for 2+ consecutive days after recovering above P30
- **THEN** the system SHALL emit a "stop_profit" signal, indicating the position should be sold to protect gains

#### Scenario: No exit signal before turning point
- **WHEN** an index has turning_point_confirmed=False
- **THEN** the system SHALL NOT emit any exit signal, regardless of greed percentile

### Requirement: Exit signal available in API response
The `/golden-pit/status` endpoint SHALL include exit signal information for each index that has a position.

#### Scenario: Exit signal in status response
- **WHEN** a client requests GET /golden-pit/status
- **THEN** each index object in the response SHALL contain an `exit_signal` field with one of: null, "half_exit", "full_exit", or "stop_profit"
- **THEN** each index object SHALL contain an `exit_reason` field with a human-readable explanation when exit_signal is not null

### Requirement: DCA service executes sell orders on exit signals
The golden pit DCA service SHALL check for exit signals on all currently held ETF positions during each execution cycle, and place sell orders when signals are triggered.

#### Scenario: Half exit execution
- **WHEN** an index has exit_signal="half_exit" AND the account holds that ETF
- **THEN** the system SHALL place a sell order for 50% of the held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with status "filled" or "failed"

#### Scenario: Full exit execution
- **WHEN** an index has exit_signal="full_exit" or "stop_profit" AND the account holds that ETF
- **THEN** the system SHALL place a sell order for all held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with strategy field set to the exit signal type
