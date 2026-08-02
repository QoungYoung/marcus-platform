## 1. 回测 Intra-Trade MAE 追踪

- [x] 1.1 在 `simulate_dca()` 的持有期遍历中新增 `min_close` 变量，追踪 `avg_entry` 之后的最低收盘价
- [x] 1.2 退出时将 `max_adverse_excursion = (min_close / avg_entry - 1)` 写入 trade dict
- [x] 1.3 在 `compute_metrics()` 中新增 MAE 统计：mean、median、min、max
- [x] 1.4 在 `compute_metrics()` 中新增 MAE 分布桶计数（<-5%, <-10%, <-15%, <-20%, <-30%）
- [x] 1.5 在报告 JSON 输出中打印 MAE 分布，验证与现有 max_drawdown 的差异

## 2. Lump Entry 反转保护

- [x] 2.1 在 `golden_pit_dca_service.py` 中新增 `_check_lump_reversal()` 函数，检测 lump_entry 后 3 天内是否出现连续 2 天 greed 下降
- [x] 2.2 在 DCA 主循环的安全制动区域（falling_knife 之后）插入 lump_reversal 检查
- [x] 2.3 触发反转时：将剩余策略切换为 uniform_5，记录 `_encode_strategy("uniform_5", trend, trend_factor, "lump_reversal")`
- [x] 2.4 在 DCA 日志中记录 `status=safety_brake`，`strategy` 包含 `lump_reversal/uniform_5`，附带触发时的 greed 序列
- [x] 2.5 添加 guard：仅当 `dca_strategy == "lump_entry"` 且 `schedule_day == 0` 且已有实际买入时检查

## 3. 深度入坑告警

- [x] 3.1 在 `golden_pit_dca_service.py` 的日报告生成函数中，遍历所有交易中指数，检查 `days_in_pit >= 30`
- [x] 3.2 符合条件的指数在报告"操作建议"段中附加 `⚠️ 深度入坑告警` 行，包含指数名称、入坑天数、当前 greed
- [x] 3.3 确保 `days_in_pit` 在报告买入候选表格中始终展示（不依赖告警才显示）—— 已存在，line 1079

## 4. 验证

- [x] 4.1 运行 `backtest_golden_pit_ultimate.py` 对至少 3 个指数验证 MAE 输出
- [x] 4.2 用历史数据模拟 lump_entry 反转场景，验证制动正确触发和不误触发
- [x] 4.3 用当前实盘数据（中证1000 8天、纳斯达克 14天）验证深度入坑告警不触发（<30天）
- [x] 4.4 用历史 50 天坑数据验证告警正确触发
