## MODIFIED Requirements

### Requirement: DCA service executes sell orders on exit signals
The golden pit DCA service SHALL check for exit signals on all currently held ETF positions in the `golden_pit` account during each execution cycle, and place sell orders when signals are triggered.

#### Scenario: Half exit execution
- **WHEN** an index has exit_signal="half_exit" AND the `golden_pit` account holds that ETF
- **THEN** the system SHALL place a sell order for 50% of the held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with status "filled" (success) or "failed" (order rejected)

#### Scenario: Full exit execution
- **WHEN** an index has exit_signal="full_exit" or "stop_profit" AND the `golden_pit` account holds that ETF
- **THEN** the system SHALL place a sell order for all held shares at limit price × 0.98
- **THEN** the DCA log SHALL record the sell with strategy field set to the exit signal type

#### Scenario: Sell order failure degrades to notice
- **WHEN** the sell order fails (no position, engine error, risk rejection)
- **THEN** the system SHALL record `status="failed"` (or fall back to `notified`) and still push the exit notice
- **THEN** the system SHALL NOT re-attempt selling the same position within the same day
