## 1. 回测数据预取扩展（t_backtest_data.py）

- [x] 1.1 新增 `prefetch_m1(symbol, trade_days, cache_dir)`：brze `stk_mins freq=1min` 逐交易日拉取，落盘 `m1/{symbol}.json`，并提供 `load_m1` 读取（回放零网络）
- [x] 1.2 扩展 `prefetch_stock_daily`：合并 tushare `daily_basic.turnover_rate`（按 trade_date）到日线记录，`load_stock_daily` 返回换手率字段
- [x] 1.3 新增筹码分布预取：tushare `cyq_perf`（成本 5/15/50/85/95 分位、获利比例）落盘为 `chips/{symbol}.json`，`load_chips` 读取
- [x] 1.4 组合/滚动引擎的预取入口接入 1.1~1.3（仅对进入做T阶段的标的预取 1min，控制体积），保持回放零网络

## 2. 假跌破守卫模块（t_stop_loss_guard.py）

- [x] 2.1 新建 `evaluate_stop(bars_up_to, stop_price, params, m1_bars, daily, chips) -> {action: stop|hold, reason, reset_stop}` 纯函数：收盘确认 → 收回幅度 → 分钟企稳 → 缩量过滤 → 支撑位感知
- [x] 2.2 `BUILD_PARAMS_DEFAULT` 新增守卫参数：`stop_close_confirm`(true)、`stop_recovery_pct`(1.0)、`stop_confirm_bars`(5)、`stop_volume_filter`(true)、`stop_support_proximity_pct`(1.5)
- [x] 2.3 单元测试覆盖 spec 全部场景（插针收回不执行、收盘确认执行、收回幅度跳过并重置基准、企稳取消、缩量过滤、支撑位全确认、参数覆盖生效）

## 3. 回测接入

- [x] 3.1 `_process_stop_loss` 接入 `evaluate_stop`：命中 hold 时跳过并重置止损基准（当日收盘价）；命中 stop 维持现有撮合价口径
- [x] 3.2 事件流新增类型并落库：`stop_warning`（盘中触及未确认）、`fake_breakdown`（假跌破跳过）、`stabilised_cancel`（企稳取消）
- [x] 3.3 `caliber_notes` 更新：1min 企稳口径、换手/筹码数据源、收盘确认成交价口径

## 4. 实盘接入

- [x] 4.1 `stop_loss_monitor` 卖出执行前调用同一 `evaluate_stop`（实时 1min/换手、最近一日 cyq_perf）
- [x] 4.2 实盘日志输出守卫判定结果与数据时点（实时/日频）

## 5. 回测验证与部署

- [ ] 5.1 用 #71 同窗口（2026-08-05~08-14，震荡市模式）重跑，对比止损卖飞次数与组合收益
- [ ] 5.2 参数敏感性：守卫全关 vs 仅收盘确认 vs 全开，对比卖飞率/收益
- [ ] 5.3 用 2026-05-18~05-29 等历史窗口回归验证，避免单窗口过拟合
- [ ] 5.4 提交、推送、部署，核对任务报告与事件流
