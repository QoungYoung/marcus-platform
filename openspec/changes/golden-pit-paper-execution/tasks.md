## 1. 执行器绑定与持仓来源切换

- [x] 1.1 `_get_executor()` 改为 `MarcusVNPyExecutor(account_id="golden_pit")`（依赖 infra 的 engine 注入）
- [x] 1.2 `_already_holding` / `_get_holding_shares` 改为查询 `golden_pit` 账户持仓（account_id 过滤）
- [x] 1.3 `_get_holdings_detail` / `_get_sector_holdings` 持仓口径切换为 `golden_pit` 账户

## 2. 买入腿落盘

- [x] 2.1 主流程买入腿循环中调用 `_place_buy_order(leg_etf, leg_amount, reason)`，成功记录 `status="filled"` + order_id
- [x] 2.2 买入失败记录 `status="failed"` + 失败原因，通知文本标注"未成交"
- [x] 2.3 金额不足一手时跳过并记录 `failed`（原因含"金额不足"）
- [x] 2.4 确认 `_get_executed_days` 去重逻辑（filled/notified 视为已执行，failed 不参与去重）

## 3. 退出信号落盘

- [x] 3.1 宽基退出/防御承接/板块二次拐点/防御撤场路径调用 `_place_sell_order`，金额→股数换算（int(amount/price/100)*100）
- [x] 3.2 卖出成功记录 `filled` + order_id + exit 策略；失败记录 `failed`（保留 notified 降级）
- [x] 3.3 `_sell_defense_on_reentry` 的防御持仓卖出同样落盘
- [x] 3.4 防止同日重复卖出（`_has_exit_notice` 按日去重校验）

## 4. 通知与日志

- [x] 4.1 成交通知包含 order_id；失败通知包含失败原因
- [x] 4.2 `_record_dca_log` 支持传递 order_id 与 failed 状态（校验现有签名）

## 5. 测试

- [x] 5.1 单元测试：mock executor，断言买入腿/退出信号调用下单函数且状态为 filled/failed
- [x] 5.2 集成测试：golden_pit 账户真实下单，断言持仓写入 golden_pit 且 stock 账户无变化
- [x] 5.3 适配 `backend/tests/test_golden_pit_sector_service.py` 等既有 DCA 测试（补充 executor mock）
- [x] 5.4 运行相关测试套件确认无回归
