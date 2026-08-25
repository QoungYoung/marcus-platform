## Why

2026-08-25 生产实测：t-mom-etf 双周轮动当日 190 次建仓尝试全部被护栏拦截（2 个 `human_confirm` 事件因盘中 regime=CAUTIOUS 升级后无人确认而卡死 → 毒化「当日建仓笔数上限 3」；随后账户底仓升至净值 53% 后又撞「总底仓 ≤60%」上限），候选信号永远停在「待处理」、零成交，且日志只报「结果 3 项」无任何失败详情。用户决定：t 账户自动建仓**不再要求任何人工确认**（所有自动来源直接放行），并放开 mom_etf 的总底仓 60% 上限、修复持仓识别与调仓节律缺陷。

## What Changes

- **移除所有自动建仓来源的人工确认升级**：`agent`/`daily_auto`/`ai_led` 的建仓尝试 SHALL 不再因 regime=CAUTIOUS、首开新标的、单笔超标准档、连续亏损期、日亏预警、近跌停、当日触犯风控（B1/B2/B4/B5/B6/B7/B8）等升级为 `human_confirm` 暂停等待——一律自动放行；上述风险分类保留为告警信息写入结果与日志。HALT 熔断（B3）仍强制拦截一切建仓。**BREAKING**（t 账户全部建仓来源行为变更，V反/趋势突破/每日自动选股同受；确认端点仅用于处理遗留 human_confirm 事件）。
- **human_confirm/pending 事件不计入当日建仓上限**：`count_today_builds` 只统计 `executed`（含单票统计），未成交事件不消耗当日配额。**BREAKING**（共享护栏口径变更，对所有 t 账户建仓来源生效）。
- **移除 mom_etf 短线档「总底仓 ≤60%」上限**：`build_sizing` 在 `mode='mom_etf'` 下不再校验总底仓上限，只保留单笔 ≤30%、单标 ≤30% 与现金约束，保证账户已持底仓时仍能按目标组合建仓。
- **持仓识别改用 t 账户实际可卖账本**：mom_etf 的「已持有」判断从「reason 含 mom_etf 的建仓事件」改为查询 t 账户实际可卖持仓（`get_sellable_ledger`），避免把已持有标的（如 8/23 由 daily_auto 建的 SH515880）当未持有重复买入。
- **双周节律改为按 mom_etf 实际成交记录计**：`_last_rebalance_date` 读 `t_build_scan_results`（source='mom_etf'、status='executed'）的最大 trade_date；调仓产生任一成交后候选置 executed（顺带解决候选永远 pending 的展示问题）。
- **调仓结果逐条落日志**：`try_rebalance` 对每条买/卖结果记录状态与原因（含 `no_price`/护栏拒绝），静默失败改为 warning 日志。

## Capabilities

### New Capabilities
- `t-account-build-gates`: t 账户建仓闸门行为——自动建仓不要求人工确认、当日建仓配额只计已成交事件。

### Modified Capabilities
- `momentum-etf-trend`: 修改轮动与调仓要求（移除总底仓 ≤60% 上限；持仓识别与调仓节律口径修正）。

## Impact

- 代码：`backend/app/services/t_build.py`（`validate_build_position` 第 7 步人工升级分流移除、`build_gateway_execute` human_confirm 分支移除、`build_sizing` mom_etf 档总仓校验）、`backend/app/services/t_db.py`（`count_today_builds` 计数口径）、`backend/app/services/t_mom_etf.py`（`_mom_positions`、`_last_rebalance_date`、`try_rebalance` 日志与候选消费）。
- 数据：生产库已存在的 2 个卡死事件（id 301/302）已人工置为 `cancelled`；本变更不涉及数据迁移。
- 测试：`backend/tests/test_t_mom_etf.py` 需补充 human_confirm 放行、总仓上限放开、持仓识别、节律计数用例；`backend/tests` 全量回归（护栏共享）。
- 风险：人工确认是风控闸门，移除后所有自动建仓即时成交——需确认这是明确的产品决策；告警信息保留以便事后审计。
