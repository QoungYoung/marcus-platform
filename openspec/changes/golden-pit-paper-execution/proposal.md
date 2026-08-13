## Why

黄金坑 DCA 主流程目前是"仅通知"模式：`execute_golden_pit_dca` 只写 `golden_pit_dca_log`（status="notified"）+ 发 QQ 通知，`_place_buy_order` / `_place_sell_order` 已实现但从未被调用；`golden-pit-exit` 规范已承诺退出卖单记录 `filled`/`failed`，代码与规范存在偏差。现在多账户底座（multi-account-paper-infra）将提供 `golden_pit` 独立模拟账户，本变更把黄金坑的买卖通知真正接入该模拟账户执行，且与正在运行的股票任务（stock 账户）完全隔离。

## What Changes

- `execute_golden_pit_dca` 主流程的买入腿改为调用 `_place_buy_order`，向 `golden_pit` 账户下限价单（现价 × 1.02），成功记 `status="filled"` + order_id，失败记 `status="failed"` 并保留通知。
- 各类退出信号（宽基退出、防御承接、板块二次拐点、防御撤场、假信号保留持仓除外）改为调用 `_place_sell_order`，向 `golden_pit` 账户下限价卖单（现价 × 0.98），成功记 `filled`，失败记 `failed` 并降级为通知。
- 卖出股数换算：退出逻辑给出金额，按现价换算为 100 股整数倍。
- `_get_executor` 改为 `MarcusVNPyExecutor(account_id="golden_pit")`；持仓查询（`_already_holding` / `_get_holding_shares` / `_get_holdings_detail`）改为读取 `golden_pit` 账户持仓。
- `paper_execute` 直接启用（不设灰度开关），上线即落单。
- 通知保留：落单成功后仍推送 QQ 成交通知（含 order_id）。

## Capabilities

### New Capabilities
- `golden-pit-paper-execution`: 黄金坑 DCA 的买入腿与退出信号在 `golden_pit` 模拟账户真实落单，记录 `filled`/`failed` 状态，失败降级通知，持仓与风控基于 `golden_pit` 账户。

### Modified Capabilities
- `golden-pit-dca-schedule`: DCA 买入腿从"仅通知"变为"真实落盘执行"。
- `golden-pit-exit`: 退出卖单执行要求补充账户维度（`golden_pit` 账户），并落实 `filled`/`failed` 状态记录。

## Impact

- 后端：`backend/app/services/golden_pit_dca_service.py`（主流程接线、持仓查询、executor 绑定）、`backend/app/services/golden_pit_sector_service.py`（如需同步退出逻辑）、`backend/app/services/scheduler_service.py`（DCA 任务执行路径不变，仅结果状态变化）。
- 依赖：`multi-account-paper-infra`（`account_id` 执行器与 `golden_pit` 账户注册）。
- 数据：`golden_pit_dca_log` 状态语义扩展（filled/failed），`paper_*` 账本新增 `golden_pit` 账户数据。
- 测试：`backend/tests/test_golden_pit_sector_service.py` 等 DCA 相关测试需适配执行路径 mock。
