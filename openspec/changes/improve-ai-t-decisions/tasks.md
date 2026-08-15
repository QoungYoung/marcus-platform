## 1. 数据迁移

- [x] 1.1 `backend/app/database.py` `_apply_ai_led_migration` 增 `t_ai_actions` 加 `outcome JSONB` 列（`ADD COLUMN IF NOT EXISTS`，幂等）；本地 + 服务器验证列存在

## 2. 审计层 outcome 支持

- [x] 2.1 `t_db.py`：`insert_ai_action` 支持 outcome 参数；新增 `update_ai_action_outcome(action_id, outcome)`；`list_ai_actions` 返回 outcome 字段（JSONB 解析）
- [x] 2.2 单元测试：outcome 写入/更新/读取；无 outcome 记录兼容

## 3. 决策质量统计

- [x] 3.1 `t_ai_agent.py` 新增 `decision_quality(symbol=None, trade_date=None)`：exec 胜率（方向归一：低吸买涨/高抛卖跌）、abandon 正确率（放弃后行情验证）、wait 转化、分布计数与平均 pct_change
- [x] 3.2 `t_account.py` `/t/ai/actions` 响应带 `quality` 聚合（按标的/日期）
- [x] 3.3 单元测试：构造 outcome 样本 → 断言 exec 胜率/abandon 正确率计算正确

## 4. 决策上下文增强（唤醒 + prompt）

- [x] 4.1 `t_bridge.py` `_recent_decisions` 返回"决策+outcome 摘要"（最近 5 条含结果）
- [x] 4.2 `t_bridge.py` 新增 `_symbol_t_stats(symbol)`：该标的近 20 次低吸后 30 分钟走向（平均 pct_change/胜率/达目标价率）、exec 胜率、abandon 正确率
- [x] 4.3 `wake_agent` 唤醒消息增加"历史模式"段 + 决策 checklist（价差盈亏比/弹药/历史胜率/连续命中）；`prompt_seeds.py` T_BUILD_SYSTEM_PROMPT 同步更新
- [x] 4.4 单元测试：唤醒上下文含最近决策结果与标的统计

## 5. outcome 回填

- [x] 5.1 `t_ai_agent.py` 新增 `record_outcome(symbol, trade_date)`：按 t_ai_actions 中当日 exec 未回填记录，拉成交后 30 分钟 m5 评估走向/盈亏，回填 outcome（方向归一）
- [x] 5.2 实盘触发：`ai_daily_review`（收盘复盘）调用 `record_outcome` 统一回填当日成交（见 design Open Questions）
- [x] 5.3 单元测试：mock 后续 bar → outcome 正确回填（买涨/卖跌归一）

## 6. 回测 outcome 对齐

- [x] 6.1 `t_backtest.py` `TBacktestEngine`：撮合后记录成交 (symbol, price, bar_idx)，回放后续 bar 计算 outcome（后续 6 根走向/是否达目标/止损，方向归一），加入 result 的决策记录
- [x] 6.2 `t_backtest_runner.py`：落库时把回测 outcome 合并写入 t_ai_actions（回测决策审计回填）
- [x] 6.3 回测 metrics 增加 `exec_win_rate_pct / abandon_correct_rate_pct / ai_wait_to_exec_rate_pct`（单标的 + `_combine_metrics` 组合）
- [x] 6.4 测试：`test_t_backtest.py` 增补 outcome 计算用例（防前视：只用成交后 bar）

## 7. 前端（可选但推荐）

- [x] 7.1 TBacktestPage 报告 metrics 卡片加 exec 胜率/abandon 正确率
- [x] 7.2 AI 决策面板每条显示 outcome 摘要（✅+0.85% / ⛔-1.5%）
- [x] 7.3 前端构建 + 部署

## 8. 集成与部署

- [ ] 8.1 全量回归：`pytest backend/tests -q`（重点 t_* 套件）
- [ ] 8.2 服务器部署：backend/worker + dsh（bridge 变更 docker cp 进卷）+ FORCE_RESEED_PROMPTS；验证 /t/ai/actions quality、唤醒上下文含历史结果
- [ ] 8.3 端到端验证：跑 1 个月回测（6 月），对比反馈前后 exec 胜率/abandon 正确率；确认 outcome 落库与质量指标输出
