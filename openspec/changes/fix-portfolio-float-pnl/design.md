## Context

`backend/app/api/portfolio.py` 的 `get_portfolio()` 中，`AccountResponse.float_pnl` 当前使用的是推导公式：

```python
total_pnl = total_asset - initial_capital
derived_float_pnl = total_pnl - realized_pnl
```

其中 `total_pnl` 来自 FIFO 交易重放（含买入手续费），`realized_pnl` 来自 trades 表的 `profit` 列（不含买入手续费）。两者计算口径不同，导致 `derived_float_pnl` ≠ `SUM(position.floating_pnl)`。

`SUM(position.floating_pnl)` 已在第 527 行计算好（`total_float_pnl`），但被后续推导值覆盖。

## Goals / Non-Goals

**Goals:**
- `AccountResponse.float_pnl` 直接等于各持仓 `floating_pnl` 之和，语义透明
- 前端无感兼容，字段名和类型不变
- 不影响 `total_pnl`、`total_return` 等其他字段

**Non-Goals:**
- 不修复 `frozen_cash` 残留问题（独立 bug，不在本次范围）
- 不修复 paper_engine `profit` 列不含买入手续费的偏差（积累问题，独立修复）
- 不改前端代码

## Decisions

**Decision 1: 用 `total_float_pnl` 替代 `derived_float_pnl`**

第 527 行已经计算好的 `total_float_pnl = sum(p.floating_pnl for p in positions)` 就是正确的持仓浮动盈亏之和。直接把它赋给 `AccountResponse.float_pnl`。

删掉 540-541 行的推导逻辑（`total_pnl = ...`, `derived_float_pnl = ...`），`total_pnl` 的计算本身在 `total_asset` 组成运算中已有，不需要额外重新赋值。

**变更前:**
```python
# Line 527: total_float_pnl = sum(p.floating_pnl for p in positions)
# Line 538-541:
total_pnl = total_asset - initial_capital
derived_float_pnl = total_pnl - realized_pnl
# Line 550: float_pnl=derived_float_pnl
```

**变更后:**
```python
# Line 527: total_float_pnl = sum(p.floating_pnl for p in positions)
# Line 550: float_pnl=total_float_pnl
```

`/portfolio/positions` 端点无需改动，它直接从 position 级别计算 float。

## Risks / Trade-offs

- **[Risk] `float_pnl` + `realized_pnl` ≠ `total_pnl`** → 这是正常现象（买入手续费造成），页面三个 KPI 本来就是独立指标，不强求数学自洽。移除推导公式后这个"不一致"会暴露出来，但这恰恰是正确行为——用户应该看到真实的仓位浮动盈亏，而不是一个强行凑平的魔法数字。
- **[Risk] 后端重启后 `float_pnl` 为 0（空仓时）** → 无影响，空仓时 `total_float_pnl = 0`，原先 `derived_float_pnl` 也趋近于 0。
