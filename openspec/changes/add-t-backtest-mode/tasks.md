## 1. 探针与历史数据预取

- [x] 1.1 探针脚本：brze stk_mins 单标的 30 交易日 m5 拉取实测（成功率/耗时/限流/行数上限），输出可行性报告，决定默认回测窗口
- [x] 1.2 数据预取器 `backend/app/services/t_backtest_data.py`：按 trade_date 逐日拉取 m5（标的）+ 指数行情 + 标的日线基准，落本地缓存 `data/t_backtest/`（幂等续拉、失败重试 3 次）
- [x] 1.3 缓存按 (symbol, bar_time) 索引 + 数据缺口标记，回放期零网络调用

## 2. 参数化重构（生产行为不变）

- [x] 2.1 `t_monitor`：抽取 `evaluate_at(cond, snapshot, now)`，`datetime.now()` 收敛为显式参数；纯字段派生（_build_vol_price 等）与数据采集分离
- [x] 2.2 `t_gateway`：抽取 `validate_order_at(symbol, side, price, volume, ctx)`（ctx 含 regime/quote/ledger/daily/risk/now/sell_in_transit），现有 `validate_order` 变薄封装
- [x] 2.3 `t_regime`：抽取 `compose_regime(day_grade, intraday_warn, hs300_drop)` 纯合成函数，实时与回测共用
- [x] 2.4 全量 backend 测试回归（test_t_* 等既有用例全绿，确认生产行为无变化）

## 3. 回放引擎与历史快照重建

- [x] 3.1 `TBacktestEngine` m5 tick 循环骨架（交易日历推进、逐日逐 tick、任务取消信号）
- [x] 3.2 快照重建器 `_build_snapshot_at`：quote.*（bar OHLC/涨跌幅/量额）、vol_ratio 回测口径（分钟量/近N日同刻均值）、minute.*/tech.*（复用 t_monitor 纯计算）、position.*（回测账本）、index.*（历史指数涨跌幅）
- [x] 3.3 触发判定接入：t_expr 表达式求值 + 通用护栏（regime 闸门/14:45/armed/冷却），时间源=回测 tick
- [x] 3.4 历史 regime 近似：L3 硬保险丝与 L2 日内前哨精确复现（历史指数当日涨跌幅），L1 用指数日线 MA20/60 + 阶段涨跌幅近似分类

## 4. 回测账本与撮合

- [x] 4.1 回测账本：底仓/可卖量（T+0 当日买入次日可卖与底仓回转区分）/当日回转量/日回转额/已实现盈亏/成本漂移/收盘未回补结转次日
- [x] 4.2 撮合器：调 `validate_order_at`（状态全注入回测上下文）+ 下一根 m5 bar close ± 滑点（默认 0.1%）成交，拦截写回测事件流
- [x] 4.3 T+0 闭环与透支警告（卖腿超卖部分标记）

## 5. LLM 复核沙盒（bridge）

- [x] 5.1 回测任务内部复核 API：触发上下文组装 + classify_escalation 6 类判定参考 + 复核结果（auto/human + 理由 + 耗时）落库
- [x] 5.2 bridge 重开 `backtest` 聊天分支：会话 key `t-backtest-{taskId}`，注册 bt_* 沙盒工具白名单（bt_place_order 落回测账本），不注册生产写工具
- [x] 5.3 规则模式开关（跳过 LLM 直接按 classify_escalation），默认 LLM 复核，可切规则对照

## 6. 落库、任务管理与报告

- [x] 6.1 表迁移（幂等，对齐 `_apply_t_build_migration` 模式）：t_backtest_tasks / t_backtest_events / t_backtest_trades / t_backtest_equity_snapshots
- [x] 6.2 任务管理 API `backend/app/api/t_backtest.py`：创建/启动/取消/查询/删除（同任务并发启动拒绝）
- [x] 6.3 指标与报告：触发次数/成交率/Agent 拦截率/单笔盈亏分布/胜率/日内闭环率/底仓成本漂移/最大回撤/总收益/买入持有基准对比 + 口径差异声明（m5 vs 30s、量比口径、regime L1 近似、成交假设、固定底仓）

## 7. Agent 入口与端到端验证

- [x] 7.1 Agent 工具 `run_t_backtest`（创建任务/查询进度/完成后返回指标摘要与报告入口）
- [x] 7.2 worker 接入回测任务执行（轮询 pending 任务，对齐 worker_commands/worker_status 控制通道模式，重活不阻塞 API）
- [x] 7.3 端到端验证 + 测试 `backend/tests/test_t_backtest.py`：回放确定性/账本与撮合规则/防前视断言/沙盒隔离断言（回测会话工具列表不含生产写工具）/单标的真实数据多日回测跑通
