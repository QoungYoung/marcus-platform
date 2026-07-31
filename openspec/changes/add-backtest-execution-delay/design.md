## Context

The `backtest_golden_pit_ultimate.py` script contains the canonical `simulate_dca()` function used for all gold pit DCA strategy optimization. It operates on historical greed/close data arrays, detecting entry signals and simulating DCA buy + dynamic exit.

**Current behavior**: After detecting a signal at `entry_idx`, the DCA buying loop starts from `entry_idx` (same day):
```python
for d in range(PIT_WINDOW_DAYS):
    day = entry_idx + d
```

**Reality constraint**: ArkVol API computes greed after market close. On day D+1 morning, the system reads day D's greed and decides. The earliest possible trade execution is day D+1. The backtest's same-day execution is impossible in production.

**Scope**: This change only affects `backtest_golden_pit_ultimate.py` and any derivative backtest scripts. The production `golden_pit_dca_service.py` already operates on T+1 timing (reads yesterday's greed, executes today) — no change needed.

## Goals / Non-Goals

**Goals:**
- Add `exec_delay` parameter (int, default 1) to `simulate_dca()` that shifts buy start by N trading days
- All exit timing (staged, trailing stop, time decay, combined) references `buy_start` instead of `entry_idx`
- Signal detection unchanged — we still detect signals using same-day greed data (the backtest "knows" history, just can't trade on it)
- Phase 1/2/3 runners pass `exec_delay` through, defaulting to 1
- Backward compatible: pass `exec_delay=0` to reproduce old results

**Non-Goals:**
- Modifying the production DCA service (already correct)
- Changing signal detection logic or thresholds
- Adding real-time intraday data to the backtest (out of scope for a historical simulation)
- Changing the DCA weight generation (`make_dca_weights()`)

## Decisions

### Decision 1: Single `exec_delay` parameter vs. separate signal-delay + exec-delay

**Chosen**: Single `exec_delay` integer that shifts buy start.

**Rationale**: The signal detection logic is unchanged (we still find signals in historical greed data). The only change is when buying starts relative to signal confirmation. A single parameter is simpler and captures the essential constraint. A separate signal-delay would imply the signal itself is detected later, which isn't how the backtest works — the backtest has perfect knowledge of history; we're only constraining execution.

**Alternative considered**: Two parameters (`signal_lag` + `exec_lag`). Adds complexity without benefit — the net effect is `buy_start = entry_idx + signal_lag + exec_lag = entry_idx + exec_delay`.

### Decision 2: `exec_delay` default value

**Chosen**: Default `exec_delay=1` (realistic).

**Rationale**: The backtest should default to realistic timing. Users who want idealized results for comparison can pass `exec_delay=0` explicitly. This follows the principle of least surprise — default behavior matches production constraints.

**Alternative considered**: Default `exec_delay=0` for backward compatibility. Rejected because the existing backtest results are already stored; new runs should be realistic by default.

### Decision 3: Exit timing reference point

**Chosen**: All exit logic (holding days, fallback days, trailing stop peak tracking) uses `buy_start` as reference.

**Rationale**: The holding period should measure from when capital is actually deployed, not from signal detection. This is both more realistic and internally consistent — if we delay buying, we should also delay the exit clock.

### Decision 4: No intraday gap filter in v1

**Chosen**: Defer the intraday "skip if open gap >2%" filter for US/HK indices to a follow-up change.

**Rationale**: The exploration data showed the gap filter would help in specific scenarios (post-signal rally days), but the core `exec_delay` fix provides 90% of the value with 10% of the complexity. Adding gap detection requires per-index open price data which the current backtest data model doesn't include.

## Risks / Trade-offs

- **[Risk] CAGR numbers will appear lower in new backtest runs** → Mitigation: Document the change clearly in output reports; include `exec_delay` value in report metadata
- **[Risk] Some indices may show different optimal entry/exit configs with delay=1** → Mitigation: This is expected and correct — the config that works with idealized timing may not be optimal under realistic constraints. Re-running optimization with delay=1 produces better real-world configs.
- **[Risk] `exec_delay` shifts buy_start beyond array bounds for signals near the end of data** → Mitigation: The existing bounds check `entry_idx + PIT_WINDOW_DAYS + MAX_HOLD >= n` already prevents this; `exec_delay` simply tightens the constraint by `exec_delay` days (a few signals near the end may be excluded)

## Migration Plan

1. Modify `simulate_dca()` signature to add `exec_delay: int = 1`
2. Update all internal references from `entry_idx` to `buy_start = entry_idx + exec_delay`
3. Pass `exec_delay` through Phase 1/2/3 runners and walk-forward
4. Add `exec_delay` to report metadata
5. Update any scripts that call `simulate_dca()` directly
6. No database migration, no API change, no frontend impact
