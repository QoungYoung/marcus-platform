## Context

`calc_position` 的仓位乘数链最终产出三层数量：`max_shares`（最大可买）、`rec_shares`（建议）、`probe_shares`（试探）。其中 rec 和 probe 各自有独立 cap：rec = `role_cap_pct% × total_asset`，probe = `10% × total_asset`。这两个值通常只有 ~10K（在 100K 账户下），对高价股买不起 100 股，被 `_round_lot` 截为 0。

而 `effective_single_cap` 在此之前已被 `min_lot_amount` 逻辑抬高到 `100 × current_price`，但抬高后的值只流入了 `max_usable → max_shares`，rec/probe 没用到它。

同时验证层 (`all_pass`) 在 shares=0 时全部通过（`0 ≤ cap` 永真），给用户错误的绿灯信号。

## Goals / Non-Goals

**Goals:**
- rec/probe 在自身 cap 买不起 100 股但 `max_shares >= 100` 时，兜底给 100 股
- `max_shares == 0` 时，`all_pass` 强制为 false 并给出可操作的原因
- `calculate_position_quantity`（backtest 共用函数）同步修复
- 保持现有乘数链不变，只在最终 _round_lot 层面兜底

**Non-Goals:**
- 不改动振幅分档、波动率系数、ADX 系数的计算逻辑
- 不改 `_round_lot` 的行为（始终保持向下取整到 100 股）
- 不引入"允许碎股"或"港股手数"等新市场规则

## Decisions

### Decision 1: rec/probe 兜底策略 — 跟 max_shares 对齐

`max_shares` 已经用了 `effective_single_cap`（含 min_lot 抬高），是系统在约束下的真实可买值。当 `max_shares >= 100` 但 rec/probe = 0 时，兜底给 100 股。

理由：`min_lot_amount` 抬高 `effective_single_cap` 时已经表达了"虽然按比例算太小，但 100 股是最小交易单位"，rec/probe 应该尊重这个决定。

备选方案（拒绝）：改为直接让 rec/probe 也用 `effective_single_cap` 而非 `role_cap_pct%`。拒绝原因：这样会完全去掉 rec/probe 的轻仓约束，probe 从 10% cap 变成可能 100% cap，语义全变。

### Decision 2: max_shares=0 时的 all_pass 判定

当 `max_shares == 0` 时，`all_pass` 强制 false，warnings 添加具体原因（cap 被哪个约束限制）。

理由：`max_shares=0` 意味着当前约束下无法建仓，此时 `all_pass=true` 是误导。0 股 ≈ 不可交易。

备选方案（拒绝）：在 validation 层加 `position_size_ok` 字段。拒绝原因：过度设计，bug fix 不需要新增字段。

### Decision 3: 警告措辞

| 场景 | 警告 |
|------|------|
| `max_shares >= 100` 但 rec/probe = 0 | "试探仓建议 100 股(最低买入)，占比偏高请注意风险" |
| `max_shares == 0` | "无法建仓：当前约束下可买 0 股 (<cap 原因>)" |

## Risks / Trade-offs

- **试探仓占比偏高**：宁德 100 股 = 36,000 (35.5%)，远超 probe 设想的 10%。兜底时加警告提醒用户。—— 可接受，因为无替代方案（要么买 100 股要么不买）
- **backtest 兼容性**：`calculate_position_quantity` 是 nasdaq backtest sandbox 的共用函数，没有 probe/rec 分层，但同样可能在 `_round_lot` 归零。修复只针对 `max_shares` 场景，不影响 backtest 其他逻辑
