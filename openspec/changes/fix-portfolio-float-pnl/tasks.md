## 1. Backend Fix

- [x] 1.1 修改 `get_portfolio()` 中的 `AccountResponse` 构造：将 `float_pnl=derived_float_pnl` 改为 `float_pnl=total_float_pnl`，删除 `total_pnl` 重新赋值和 `derived_float_pnl` 推导公式（line 538-551）
- [ ] 1.2 重启后端服务验证 `/api/v1/portfolio` 响应中 `float_pnl == sum(positions[*].floating_pnl)`

## 2. Verification

- [ ] 2.1 在云服务器上确认比亚迪只有一只持仓时，浮动盈亏与持仓表格中的单只浮盈一致
- [x] 2.2 确认其他页面（TradingPage、BacktestPage）无异常，因为 `float_pnl` 字段名不变
