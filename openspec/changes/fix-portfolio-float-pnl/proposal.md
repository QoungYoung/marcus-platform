## Why

Portfolio 页面的"浮动盈亏"显示的是一个推导值 `total_pnl - realized_pnl`，而非各持仓浮动盈亏之和。推导公式的两个输入来自不同的计算路径（FIFO 现金流重放 vs DB profit 列），存在系统性偏差（买入手续费差额、冻结资金残留、成交价差异），导致用户看到 +585 浮动盈亏时，实际持仓只有一只亏损的比亚迪。用户直觉"浮动盈亏 = SUM(每只持仓盈亏)"是对的，代码应该匹配这个直觉。

## What Changes

- **浮动盈亏改为持仓求和**：`AccountResponse.float_pnl` 从 `derived_float_pnl` 改为 `SUM(position.floating_pnl)`
- **AccountResponse 删掉没必要的推导逻辑**：移除 `total_pnl - realized_pnl` 的推导公式
- **PortfolioSummary 三个字段保持自洽**：`total_return = total_pnl`（总盈亏），`float_pnl = SUM(持仓浮盈)`（浮动盈亏），前端不再出现语义不符的数字

## Capabilities

### New Capabilities
<!-- None needed — this is a bug fix within existing portfolio capability -->

### Modified Capabilities
- `portfolio`: 浮动盈亏的计算方式从"推导公式"改为"持仓浮盈求和"，同时保证 account 响应中各字段语义一致

## Impact

- **Backend**: `backend/app/api/portfolio.py` 第 540-551 行，移除 `derived_float_pnl` 推导，改为 `total_float_pnl`（已在第 527 行计算好）
- **Frontend**: 无需改动，`Account.float_pnl` 字段名不变，只是值变正确了
- **API contract**: 不变，`float_pnl` 仍为 `float` 类型
