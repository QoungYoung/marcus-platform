## Context

做T系统生产链路（TMonitor 30s 轮询 → t_expr 表达式求值 → t_triggers → DSH Agent 复核 → t_gateway 三阶校验 → PaperTradingEngine 撮合）已在运行，做T Agent 选股建仓（t_build）已落地。现状约束：

- 历史分钟线唯一长窗口来源是 brze tushare 代理 `stk_mins`（腾讯/新浪仅最近数日）；stk_mins 无换手率字段。
- `t_monitor._build_snapshot` 与 `t_gateway.validate_order` 深度绑定实时数据源（`fetch_tencent_quote`/`fetch_minute_bars`/`compute_regime`/`get_sellable_ledger`/`t_db` 当日状态/`paper_orders`）与 `datetime.now()`。
- `t_expr.evaluate_expression` 是纯函数（快照 → bool），`t_monitor` 的技术指标计算（_sma/_ema/_calc_macd/_calc_kdj/_calc_rsi/_build_vol_price）与 `t_gateway.classify_escalation` 均为纯计算，可直接复用。
- 历史 regime 的 L1 层依赖 `market_diagnosis` 表（实盘当日诊断），历史窗口无此数据。
- DSH bridge 的 `backtest` 聊天分支曾因指向生产会话而下架——回测会话必须工具隔离。

## Goals / Non-Goals

**Goals:**
- 单标的 × 多交易日（默认近 30 个交易日）、m5 粒度的监控条件历史回放，回答"条件触发准不准、赚不赚钱"。
- 复用生产触发判定核心（t_expr + 护栏 + 网关规则），通过参数化重构注入历史上下文，生产行为零变化。
- 真实 LLM 复核，但严格沙盒隔离（回测写操作绝不触达生产交易通道）。
- 全事件流落库 + 指标报告 + 口径差异显式标注。

**Non-Goals:**
- 不做"建仓 → 做T → 闭环"全链路回测（建仓环节回测留待未来扩展；本次初始底仓用固定假设）。
- 不做 m1 粒度回放（数据量与耗时 5 倍，后续按需）。
- 不做多标的多日组合级回测（量比基准/regime 全局共享的横向扩展后续做）。
- 不改动生产触发与执行的行为语义（重构只参数化，不改变逻辑）。

## Decisions

### D1. 回放引擎：独立单进程 m5 tick 循环，不复用 TMonitor 线程
回测是离线批处理：无需 30s 轮询、并发取价、jitter、盘中/盘后切换。设计 `TBacktestEngine`：按日按 tick 遍历预取数据，每 tick 执行"重建快照 → 表达式求值 → 通用护栏 → 触发 → 复核 → 撮合"。
- **理由**：TMonitor 的线程循环/缓存/并发是实时性产物，回放需要确定性（同数据同结果，除 LLM 复核环节外）。
- **替代**：运行时 monkey-patch `fetch_*`/`compute_regime` 后直接驱动 `TMonitor._round`——不可测试、时间源难控、并发副作用多，否决。

### D2. 参数化重构（生产行为不变），不重写
- `t_monitor`：把 `_evaluate_condition`/`_pass_common_gates` 中的 `datetime.now()` 收敛为显式 `now` 参数（新增 `evaluate_at(cond, snapshot, now)`），`_build_snapshot` 拆出纯字段派生（`_build_vol_price` 等已是），数据采集与字段派生分离。
- `t_gateway`：抽取 `validate_order_at(symbol, side, price, volume, ctx)`，`ctx = {regime, quote, ledger, daily_state, risk_state, now, sell_in_transit}`；现有 `validate_order` 变成用实时 ctx 调 `validate_order_at` 的薄封装。
- `t_regime`：抽取 `compose_regime(day_grade, intraday_warn, hs300_drop)` 纯合成函数；实时路径与回测路径共用。
- **理由**：回测与生产共享同一套判定规则，避免"回测复刻"与"生产实现"漂移；重构有既有 `test_t_*` 测试兜底回归。

### D3. 数据预取：brze stk_mins 按 trade_date 逐日拉取 + 本地缓存
`fetch_brze_stk_mins` 已支持 `trade_date` 单日参数。预取器按交易日逐日拉取 m5（每标的每交易日 48 根），落 `data/t_backtest/{task_id}/`（parquet 或 sqlite），同时预取指数行情与标的日线（量比基准、tech 日线基准、regime 近似）。
- **理由**：stk_mins 单次调用有行数上限，逐日拉取天然规避；缓存避免回放中网络抖动与 brze 限流（卖家要求单线程串行 + ≥1s 间隔）。
- **防前视**：缓存按 `(symbol, bar_time)` 索引；回放评估点只读 `bar_time <= tick` 的数据；日线基准只用 `trade_date < T` 的数据。预取完成后回放阶段零网络调用。
- **风险预案**：预取失败重试 3 次 + 缺口标记（对应 spec "数据缺口跳过"）。

### D4. 快照重建器：与实盘同构，数据源换成历史查询
`TBacktestEngine._build_snapshot_at(symbol, cond, tick, data_ctx)` 产出与 `TMonitor._build_snapshot` 相同结构的字段字典：
- `quote.*`：当前 m5 bar 的 OHLC/涨跌幅/量额（turnover_rate 置 0 并标注不可用）。
- `vol_ratio`：**回测口径** = 当前 bar 成交量 / 近 N 日同刻平均成交量（实盘口径为换手率×时段伸缩/同刻均值，两者在报告中并列标注）。
- `minute.*`/`tech.*`：用截至 tick 的历史 m5 bar 重算（复用 t_monitor 纯计算函数，无口径差异）。
- `regime.*`：历史 regime（见 D5）。
- `position.*`：回测账本当前状态。
- `index.*`：历史指数当日涨跌幅。

### D5. 历史 regime 近似合成
复用 `compose_regime`（D2 抽取），三输入历史化：
- L3 硬保险丝：HS300 当日涨跌幅 ≤ -2%（历史指数 m5 或日线算当日涨跌幅）。
- L2 日内前哨：任一指数当日涨跌幅 ≤ -0.8%（历史数据可精确复现）。
- L1 日频基准：`market_diagnosis` 无历史 → 用指数日线 MA20/60 关系 + 阶段涨跌幅近似分类 trend/oscillation/extreme，**报告标注"L1 为近似，与实盘档位可能不同"**。

### D6. 撮合器：validate_order_at + 下一根 bar 成交
回测撮合调 `validate_order_at`（状态全部来自回测上下文），通过后在**下一根 m5 bar 的 close ± 滑点（默认 0.1%，可配置）**成交：
- **理由**：与实盘"触发后下一轮轮询才下单"的时滞一致（保守口径）；同根 close 成交为乐观替代，后续可配置对比。
- 账本：可卖底仓（T+0 当日买入次日可卖 与 底仓回转区分）、当日回转量、日回转额、已实现盈亏、底仓成本漂移；收盘未回补底仓结转次日（卖腿超卖部分记"透支"并警告）。
- 风控状态（STOP_ALL/连续亏损/日亏损熔断）按回测日账本在回测内演化，不读生产状态。

### D7. LLM 复核沙盒隔离（bridge 层）
bridge 重开 `backtest` 聊天分支，但会话 key 用 `t-backtest-{taskId}`（独立于 `chat:t-agent-*`/`trade:*`）：
- 该会话注册回测沙盒工具集：`bt_place_order`（POST 回测任务内部撮合 API，仅落回测账本）、`bt_*` 读工具（回测快照/触发上下文）；**不注册** `place_order`/`cancel_order` 等生产写工具。
- 系统提示词声明"回测模式，下单进入沙盒"。
- 复核流程：触发 → 组装复核上下文（触发快照 + classify_escalation 6 类升级判定）→ 真实 LLM 决策（auto/human）→ auto 走撮合 / human 记"升级不成交"。
- **理由**：LLM 决策不可复现，全量落库（prompt/决策/理由/耗时）是唯一审计手段；同时保留"纯规则模式"（跳过 LLM 直接按 classify_escalation）作为对照实验与成本控制选项（配置开关，默认 LLM）。

### D8. 结果落库与指标
新增表：`t_backtest_tasks`（参数/状态/底仓假设/错误）、`t_backtest_events`（触发/复核/拦截/缺口全事件流）、`t_backtest_trades`（成交明细，含触发价 vs 成交价滑点实测）、`t_backtest_equity_snapshots`（每日净值）。迁移沿用 `_apply_t_build_migration` 的幂等模式。指标计算对齐现有 `BacktestEngine` 报告风格（胜率/回撤/收益），另加做T专属：成交率、Agent 拦截率、日内闭环率、底仓成本漂移。

### D9. 任务执行模型
任务创建由 API 进程写库（pending），worker 进程轮询启动执行（对齐现有 worker_commands/worker_status 控制通道模式），执行放 worker（重活不阻塞 API）。Agent 工具 `run_t_backtest` 走 API 创建/查询，任务运行中返回进度。

## Risks / Trade-offs

- **brze stk_mins 数据可得性/行数限制** → 探针先行（单标的 30 日 m5 拉取成功率/耗时/限流实测），预取按日 + 重试 + 缺口标记；若 stk_mins 权限受限，降级方案为腾讯/新浪日线级近似（需重新评估 scope）。
- **m5 粒度 vs 实盘 30s 轮询的触发精度差** → 报告标注；后续可加"bar 内高低价触发的乐观/保守双口径"。
- **量比口径差异导致触发边界漂移** → 报告并列两种口径的触发次数对比；这是回测本身要回答的问题之一。
- **regime L1 近似可能使档位判定偏离实盘** → 报告标注 + regime 档位分布统计（回测期 ACTIVE/CAUTIOUS/HALT 天数）。
- **LLM 复核不可复现 + 成本** → 决策全落库；提供规则模式开关做对照；复核用 flash 级模型控制成本。
- **重构 t_monitor/t_gateway 引入生产回归** → 保持函数签名兼容 + 既有 test_t_* 全量回归 + 重构与回测功能分任务提交。
- **回测会话误配生产工具（历史教训）** → 沙盒工具集白名单注册，回测会话 key 与生产会话隔离，测试断言"回测会话工具列表不含生产写工具"。

## Migration Plan

1. 纯重构（D2/D5 参数化）先合入，跑全量 backend 测试回归，生产行为不变。
2. 数据预取层 + 探针脚本先行验证 brze 可得性。
3. 回放/账本/撮合/落库逐步合入（功能开关默认关闭，不影响生产做T链路）。
4. 回测任务执行挂 worker；bridge 回测分支与会话沙盒最后接通。
5. 回滚：回测为新增能力，停用配置开关即完全隔离；重构部分可 revert 单独提交。

## Open Questions

- 前端回测结果页是否需要（当前 B1 Agent 工具入口已覆盖，页面属体验增强，可后议）。
- 报告导出格式（CSV/JSON/HTML）与现有 BacktestPage 导出风格是否统一（实现期定，不影响 spec 与任务分解）。
- 回测窗口默认长度是否按探针结果调整（30 日 → 更长窗口需评估预取耗时）。
