## Why

做T系统已在生产运行（TMonitor 30s 轮询触发 → DSH Agent 复核 → 网关风控执行），但监控条件（自由表达式/阈值）从未经过历史数据验证——"触发得准不准、赚不赚钱"完全是未知数。做T Agent 的选股建底仓能力已落地（`t_build`，change `add-t-position-building`），系统进入可完整运转阶段，缺的最后一块拼图是**历史验证闭环**：用历史分钟数据回放做T监控条件，量化触发质量与收益表现，为条件参数（触发价、量比阈值、regime 闸门、止损/止盈）提供数据依据。

## What Changes

- **历史数据预取层**：brze tushare 代理 `stk_mins`（m5 按 trade_date 逐日拉取）+ 指数（HS300/上证/深成指）+ 标的日线（量比基准/技术指标基准），落本地缓存（parquet/sqlite），**按 bar 时间戳严格截止，杜绝前视**。
- **回放引擎**（m5 tick 循环，等价 TMonitor 评估）：历史快照重建器（quote/minute/tech/regime/position 字段全部由历史数据重算，口径差异显式标注）→ 复用 `t_expr.evaluate_expression` 求值 → 复用通用护栏（时间源注入）→ 命中写回测触发事件。
- **回测账本 + 撮合器**：复刻 `validate_order` 三阶校验规则（regime/行情/可卖底仓/日账本/风控状态全部注入），成交按**下一根 m5 bar close ± 0.1% 滑点**模拟，T+0 闭环（高抛减仓→低吸买回→底仓恢复）显式建模；初始底仓用**固定假设**（如 1000 股 @ 回测首日价，可配置）。
- **真实 LLM 复核（沙盒隔离）**：每个回测任务创建专用会话（`t-backtest-{taskId}`），工具集为回测沙盒版（`bt_place_order` 落回测账本，写工具绝不触碰生产 `paper_orders`/`/trades`）；复核决策（auto/human）与理由完整落库（LLM 不可复现，审计为唯一可信手段）；`classify_escalation` 6 类升级规则作为 LLM 的输入参考，两者可对比校准。
- **结果与报告**：触发/复核/成交/风控拦截全事件流落库（`t_backtest_*` 表），指标含触发次数、成交率、Agent 拦截率、单次盈亏、胜率、日内闭环率、底仓成本漂移、最大回撤、买入持有基准对比、滑点实测（成交价 vs 触发价）。
- **入口**：REST API（任务 CRUD/详情/报告）+ DSH Agent 工具 `run_t_backtest`（B1 起步，Agent 在对话里发起回测与查看结果）。
- **参数化重构**（生产行为不变）：`t_monitor` 抽出接受 `now` 的评估纯函数；`t_gateway` 抽出 `validate_order_at(...)`（状态全注入），现有 `validate_order` 变薄封装。
- **范围起步**：单标的 × 多交易日（D2），m5 粒度（A1），近 30 个交易日（可配置）。
- **口径差异标注**：m5 vs 实盘 30s 轮询、量比（分钟量均值 vs 换手率）、regime L1（market_diagnosis 无历史 → 指数日线近似）、成交假设、固定底仓——报告逐条列出。

## Capabilities

### New Capabilities

- `t-backtest`: 做T监控条件的历史回测验证——历史分钟数据预取、m5 回放触发、回测账本与撮合、真实 LLM 复核沙盒、结果落库与指标报告、Agent 发起入口。

### Modified Capabilities

<!-- 无：现有做T spec（t-monitor-trigger/t-regime-gate/t-execution-risk/t-position-building）的对外行为不变，
     回测是新增的独立能力，参数化重构不改变生产行为。 -->

## Impact

- **Backend 新增**：`backend/app/services/t_backtest.py`（预取/回放/账本/撮合）、`backend/app/services/t_backtest_data.py`（brze m5 预取与缓存，可并入 t_data_sources）、`backend/app/api/t_backtest.py`（任务 CRUD/报告）、`backend/app/models/t_backtest_orm.py`（落库表 + 迁移）。
- **Backend 重构（行为不变）**：`t_monitor.py`（评估函数参数化 `now`）、`t_gateway.py`（`validate_order_at` 抽取）、`t_data_sources.py`（brze stk_mins 按日拉取封装）。
- **Bridge（DSH）**：重开 `backtest` 聊天分支（指向回测会话而非生产会话——此前下架正是因为指向生产会话）；回测会话沙盒工具注册（`bt_place_order` 等）。
- **Agent**：新增 `run_t_backtest` 工具（创建/查询回测任务）。
- **前端（可选，后续）**：回测结果页。
- **数据**：brze stk_mins 历史分钟线（m5，按日），指数日线/分钟线；本地缓存目录 `data/t_backtest/`。
- **测试**：`backend/tests/test_t_backtest.py`（回放/账本/撮合/防前视/沙盒隔离）。
