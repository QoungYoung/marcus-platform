## Context

黄金坑 DCA 主流程 `execute_golden_pit_dca`（`backend/app/services/golden_pit_dca_service.py`）目前是"仅通知"模式：买入腿与退出信号只写 `golden_pit_dca_log`（`status="notified"`）并生成 QQ 通知文本，`_place_buy_order` / `_place_sell_order`（L618/L649）已实现下单能力但全库无调用点。`golden-pit-exit` 规范已要求退出卖单记录 `filled`/`failed`，代码与规范存在偏差。前置变更 `multi-account-paper-infra` 提供 `account_id` 执行器与 `golden_pit` 独立账户（初始资金 200,000），本变更在其上接线。

## Goals / Non-Goals

**Goals:**
- 黄金坑 DCA 买入腿真实落盘到 `golden_pit` 模拟账户。
- 黄金坑各类退出信号真实卖出 `golden_pit` 账户持仓。
- 状态语义落地：`filled`（成交）/ `failed`（失败）/ `notified`（降级通知）。
- 持仓与风控完全基于 `golden_pit` 账户，与股票任务（stock 账户）零耦合。
- `paper_execute` 直接启用，无灰度开关。

**Non-Goals:**
- 不做多账户底座本身（前置 change 负责）。
- 不改变 DCA 金额计算、权重、安全制动等策略逻辑（只改"通知→落单"这一段）。
- 不改做 T agent 相关（未来独立 change）。

## Decisions

### D1: 执行器绑定 golden_pit 账户，直连 PaperTradingEngine
`_get_executor()` 改为 `MarcusVNPyExecutor(account_id="golden_pit")`（依赖前置 change 的 engine 注入能力），不走 vnpy bridge。
- 备选：复用全局 bridge（`get_bridge()`）→ 会把黄金坑交易写进 stock 账户，违背隔离目标，弃用。
- 后果：黄金坑订单写入 PG `paper_*` 表（account_id='golden_pit'），成交由 paper engine `match_order` 完成，与股票任务并发安全（按账户行锁）。

### D2: 买入腿接线点
`execute_golden_pit_dca` 中 `_build_buy_legs` 循环（L1507 附近）逐腿执行：
1. `leg_amount <= 0` 跳过（保持现状）。
2. 调 `_place_buy_order(leg_etf, leg_amount, reason)`。
3. 成功 → `_record_dca_log(status="filled", order_id=...)`；失败 → `_record_dca_log(status="failed")`，通知文本标注"未成交"。
- `_get_executed_days` 去重逻辑已把 `filled`/`notified` 都视为已执行，无需改动；新增 `failed` 不参与去重（次日可重试）。
- 注意：现有 `_place_buy_order` 用 `amount/current_price/100` 取整股数，符合一手规则。

### D3: 退出信号接线点
退出路径（宽基退出、防御承接、板块二次拐点、防御撤场、`_sell_defense_on_reentry`）：
1. 计算卖出金额（现有 `amount` 字段）→ 用 `_get_quote` 现价换算股数 `int(amount/price/100)*100`（不足一手则按实际持仓整数手）。
2. 调 `_place_sell_order(leg_etf, shares, reason)`。
3. 成功 → `filled` + order_id；失败 → `failed`（可保留原 `notified` 降级路径），通知保留。
- 持仓金额来源统一改用 `_get_sector_holdings` / `_get_holdings_detail` 的 `golden_pit` 账户口径（前置 change 已把持仓查询按账户过滤）。

### D4: 持仓查询切换账户
`_already_holding` / `_get_holding_shares` 改为查询 `paper_positions` 中 `account_id='golden_pit'` 的行（前置 change 提供账户过滤的 ORM/查询封装）；`_get_holdings_detail` 同步过滤。`stock` 账户的 ETF 持仓不影响黄金坑判断（spec 场景已固化）。

### D5: 失败与重试语义
- `failed` 状态不参与"已执行日"去重 → 次日窗口仍可补投（与安全制动语义一致：`safety_brake`/`aborted` 也不参与去重）。
- 退出卖单失败当日不重试（`_has_exit_notice` 已按日去重），次日再评估。
- 通知文本：成交通知含 order_id；失败通知含原因（资金不足/风控拒绝/引擎异常）。

### D6: 测试策略
- 单元测试 mock `_get_executor`，断言买入腿/退出信号调用 `_place_buy_order`/`_place_sell_order` 且状态为 filled/failed。
- 集成测试：golden_pit 账户真实下单（PG），断言 paper_positions 出现对应账户持仓、stock 账户不受影响。
- 适配现有 `test_golden_pit_sector_service.py`（其 mock 了 `_build_buy_legs` 等，需补充 executor mock）。

## Risks / Trade-offs

- [黄金坑与股票共用同一 paper engine 代码路径，接线引入回归] → 执行器按 account 隔离 + 测试覆盖"stock 账户无新写入"断言。
- [失败状态堆积导致重复尝试] → `failed` 不参与去重是刻意的（次日重试），用 `_has_exit_notice` 防同日重复卖出；买入侧以 `_get_executed_days` + 当日日志兜底。
- [现价获取失败导致无法换算股数] → 沿用现有 `_get_quote` 失败即返回原因，降级 `failed`/通知，不硬塞。
- [golden_pit 账户资金不足（20 万上限）] → 买入腿失败记录 `failed` + 资金不足原因，通知人工介入；不调整股票账户。
- [前置 change 未完成时无法接线] → 本变更明确依赖 `multi-account-paper-infra` 的 `account_id` 执行器能力，实施顺序先 infra 后本变更。
