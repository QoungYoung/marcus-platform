## 1. Global Macro Overlay in golden_pit_service.py

- [x] 1.1 Add `_parse_global_macro_overlay(gcf_data)` method: parse sentiment_score, sentiment_label, compute global trend from series, derive liquidity_gate and global_macro_coefficient
- [x] 1.2 Add `global_macro` field to status response dict (both `_get_status_from_api` and `_get_status_from_db` paths)
- [x] 1.3 Global macro exit: half_exit when sentiment_score > 80 and turning_point_confirmed (via `_apply_global_macro_to_indices` post-processing)
- [x] 1.4 Add `turning_validation` field: when turning_point_confirmed but global trend declining, cap position_tier to pre_turn (via `_apply_global_macro_to_indices` post-processing)
- [x] 1.5 Include global_macro summary in morning report (`format_morning_report`)

## 2. DCA Execution Guards in golden_pit_dca_service.py

- [x] 2.1 Add liquidity gate check at top of `execute_golden_pit_dca()`: fetch global_macro from golden_pit_service, skip all buys if liquidity_gate == "closed"
- [x] 2.2 Apply `global_macro_coefficient` to daily_amount calculation after resonance_multiplier
- [x] 2.3 Log macro coefficient and gate status in summary output ("global_liquidity_gate_closed" / macro coefficient display)
- [x] 2.4 Exit signal resolution: global macro exit flows through `_apply_global_macro_to_indices`, DCA sell logic already handles all exit types correctly

## 3. API Response

- [x] 3.1 GET /golden-pit/status includes `global_macro` object with all required fields (via status dict from service)
- [x] 3.2 Each index in status response includes `turning_validation` field when divergent (set by `_apply_global_macro_to_indices`)

## 4. Verification

- [x] 4.1 Unit tests pass: `_parse_global_macro_overlay` thresholds and trend detection verified
- [x] 4.2 Unit tests pass: `_apply_global_macro_to_indices` turning validation, global exit, signal priority verified
