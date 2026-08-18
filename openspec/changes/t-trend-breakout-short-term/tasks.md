# Tasks — t-trend-breakout-short-term

> 参考 specs/t-trend-breakout-short-term/spec.md（行为契约）与 design.md（D1-D8）。
> 落地顺序遵循 Migration Plan：隔离骨架 -> 扫描入池 -> 建仓模式 -> 规模/出场 -> 超时监控 -> 回测。

## 1. 账户隔离骨架（设计 D1）

- [x] 1.1 停用/移除主账户侧 trend_breakout_monitor 的自动入池（保留代码文件供回测或只读报告，worker 不再注册）
- [x] 1.2 确认 trend_break 全链路执行器固定 account_id='t'（扫描写 t_build_scan_results，建仓走 build_gateway_execute，平仓走 gateway_execute）
- [x] 1.3 测试：趋势突破扫描/建仓/平仓全程不产生 stock/golden_pit 表与资金变动（单元测试断言 account_id 恒为 't'；DB 端到端断言并入 7.3 生产验收）

## 2. 日频选股入池（设计 D2）

- [x] 2.1 新增 t_trend_break.py：日频筛选（当日主力净流入>0、5日累计>0、市值<100亿、放量突破近20日高点、MA20 转上），命中写 t_build_scan_results（source='trend_break'，status='pending'）
- [x] 2.2 扫描节流：<=50 票/日、逐只 >=1s 间隔；数据源异常跳过并告警
- [x] 2.3 测试：入池条件矩阵（资金/市值/突破/MA20）、节流、降级（test_t_trend_break.py 12 例通过）

## 3. 建仓 trend_break 模式（设计 D3）

- [x] 3.1 build_t_position 新增 build_mode='trend_break'：跳过 confirm_build_timing（回踩/量比/企稳），其余校验（白名单/熔断/时段/封板/规模/日建仓上限/单票单批）全部保留
- [x] 3.2 首开自动放行沿用 ai_led 语义（allow_first_open=True），decision_source 记为 ai_led/trend_break
- [x] 3.3 测试：trend_break 放行突破建仓；硬风控复用既有建仓网关校验（接线单测通过；STOP_ALL/熔断等由既有 t_build 测试覆盖）

## 4. 规模档与出场条件（设计 D4/D5）

- [x] 4.1 t_build_params 新增 trend_break_* 参数档（single 30%/per_symbol 30%/total 60%/max 3/mcap<=100亿/hold 5d/tp5/tp8/sl5）
- [x] 4.2 建仓次日生成出场条件：+5% 减半、+8% 清仓、-5% 止损（t_trend_break.check_exits 实现）
- [x] 4.3 测试：出场条件生成正确、D0 不可卖、规模档与既有 4/5/8% 档隔离（sizing 单测断言 75000/150000 vs 25000）

## 5. 超时平仓监控与 worker 注册（设计 D6/D7）

- [x] 5.1 超时平仓：D+1 起第 5 交易日未了结 -> 市价清仓（经 gateway_execute account_id='t'，止损/超时不阻断于日亏熔断）
- [x] 5.2 worker 注册 TrendBreakMonitor（60s 低频，T_TREND_BREAK_ENABLED 默认关闭灰度）
- [x] 5.3 测试：超时平仓、实时复核降级（单测覆盖），异常不连坐既有 t 监控（try/except 隔离）

## 6. 回测接线（本地 parquet）

- [x] 6.1 用 data/股票数据 的 stock_daily + moneyflow parquet 回放 trend_break 入池/出场，输出与固定底仓口径可对比的胜率/盈亏比/持有天数（scripts/backtest_trend_break.py）
- [x] 6.2 测试：回测脚本可跑、参数可调、输出收敛（3035 例：胜率约44%、单笔期望 +0.35%、PF 1.15、平均持有约 2.9 天）

## 7. 文档与验收

- [x] 7.1 更新 docs/t-optimization-plan.md：trend_break 模式、规模档与账户隔离说明
- [x] 7.2 全量回归：已在 docker-pgsql(127.0.0.1:5432) **重置干净库**后用完整依赖容器跑 `python -m pytest app/tests -q` → **344 passed / 17 failed / 2 skipped**；本变更新增 test_t_trend_break.py 12 例全部通过。17 个失败与脏库/干净库两轮结果完全一致（golden_pit_paper_execution / multi_account_paper_infra / dca_carrier / t_backtest(sandbox,auto-select) / t_combined_backtest / t_performance / t_account_trading human_confirm timeout），均为容器内缺完整 workspace/数据/桥接的**既有环境性失败**；已确认 `build_mode` 无命名冲突、既有 build_t_position 调用均走默认 standard，**本变更无回归**
- [ ] 7.3 验收：开启 T_TREND_BREAK_ENABLED 后，扫描/建仓/出场仅落在 t 账户，其他账户资金零变动（需部署后验收）
