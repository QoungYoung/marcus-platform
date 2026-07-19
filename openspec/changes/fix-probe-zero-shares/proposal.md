## Why

`calc_position` 对大部分标返回 `probe_shares=0` / `rec_shares=0`，但 `all_pass=true`。原因：`rec`/`probe` 层用 `role_cap_pct% × total_asset`（通常 10%）做 cap，高价股买不起 100 股被 `_round_lot` 截为 0，而 `min_lot_amount` 抬高后的 `effective_single_cap` 没有传递给 rec/probe 计算。同时验证层对 0 股无感知，始终判通过。

## What Changes

- rec/probe 层在 `_round_lot` 归零但 `max_shares >= 100` 时，兜底给 100 股并附警告
- 当 `max_shares == 0` 时，`all_pass` 必须为 false 并给出明确原因
- `calculate_position_quantity`（nasdaq backtest sandbox）同步修复

## Capabilities

### New Capabilities

- `position-sizing-floor`: 试探仓/建议仓的 100 股最低买入兜底逻辑，以及 max_shares 为 0 时的不可建仓判定

### Modified Capabilities

<!-- None: existing specs don't cover calc_position internals -->

## Impact

- `backend/app/api/indicator.py` — `calc_position` 端点（rec/probe 计算 + all_pass + validation）
- `backend/app/api/backtest.py` — `calc_position_sandbox` 同步修复
- `backend/app/services/candidate_pool_monitor.py` — 依赖 `probe_shares` 建仓，受益于此修复
- `backend/app/services/long_term_pool_monitor.py` — 同上
