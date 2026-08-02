## 1. Per-Index 时间配置

- [x] 1.1 在 `golden_pit_service.py` 的 `CHINA_INDICES` 中为每个指数添加 `buy_time` 和 `buy_time_pit` 字段及默认值
- [x] 1.2 在 `golden_pit_dca_service.py` 中新增 `_get_buy_time(fund_code, is_pit_day)` 函数，根据当前是否为黄金坑日返回目标买入时间
- [x] 1.3 在 DCA 日志的 `strategy` 字段中追加买入时间信息（如 `time=09:37`）

## 2. 分时调度

- [x] 2.1 在 `config/tasks.yaml` 中将 `golden_pit_dca` 拆为两个触发器：`golden_pit_dca_morning`（cron: `36 9 * * 1-5`）和 `golden_pit_dca_afternoon`（cron: `44 14 * * 1-5`）
- [x] 2.2 修改 `_execute_golden_pit_dca_task()` 函数，接收当前批次的时间槽参数（`morning` / `afternoon`），只执行 `buy_time` 匹配的指数
- [x] 2.3 添加批次去重逻辑：通过查询 DCA 日志确保同一指数同日不重复执行
- [x] 2.4 处理边界：非 A 股交易时间（如恒生指数 09:30-16:00 含午休）的 ETF 正确匹配

## 3. 回退兼容

- [x] 3.1 保留单次触发 fallback：若 `golden_pit_dca` 旧任务仍存在，不冲突
- [x] 3.2 未配置 `buy_time` 的指数默认为 `09:36`，确保向后兼容

## 4. 验证

- [ ] 4.1 验证分时过滤逻辑：早盘批次只执行早盘指数，尾盘批次只执行尾盘指数
- [ ] 4.2 验证 PIT/非PIT 日时间切换：坑日使用 `buy_time_pit`，非坑日使用 `buy_time`
- [ ] 4.3 验证去重逻辑：同一指数不会在两个批次中重复执行
