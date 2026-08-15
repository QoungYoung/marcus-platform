## 1. 数据库迁移

- [x] 1.1 `backend/app/database.py` 新增 `_apply_ai_led_migration`（幂等）：`t_ai_actions` 表（id/session_id/trade_date/symbol/action_type/input_snapshot JSONB/output JSONB/gateway_result JSONB/created_at，trade_date+symbol 索引）
- [x] 1.2 同迁移内：`t_conditions` 增加 `publisher VARCHAR(16) DEFAULT 'rule'` 与 `session_id VARCHAR(64)` 列（`ADD COLUMN IF NOT EXISTS`）
- [ ] 1.3 在 `init_db()` 调用链挂接 `_apply_ai_led_migration`，本地 + 服务器验证表与列存在

## 2. 网关 ai_led 档位

- [x] 2.1 `t_gateway.py`：`validate_order_at` / `gateway_execute` 接受 `decision_source='ai_led'`（与 agent 同档风控，不豁免任何校验）
- [x] 2.2 `t_build.py`：`build_t_position` / `build_gateway_execute` 支持 `decision_source='ai_led'`（建仓首开沿用 daily_auto 先例自动放行，时段/规模/熔断/资金上限全链不变）
- [x] 2.3 单元测试：ai_led 主动买卖过网关/被拒记录 reason；ai_led 建仓成功与升级人工路径

## 3. AI 决策编排服务

- [x] 3.1 新增 `backend/app/services/t_ai_agent.py`：`handle_ai_decision(trigger, context)` 解析 AI 输出（exec/wait/abandon/update_condition）并路由（exec→gateway_execute(ai_led)，update_condition→条件更新），每步写 `t_ai_actions`
- [x] 3.2 同模块 `ai_select_and_build()`：候选池优先（source=pool）→ 空则 scan 补充 → AI 决策建仓（build_t_position ai_led）
- [x] 3.3 同模块 `ai_daily_review()`：拉当日 t_ai_actions → 唤醒 AI 复盘 → 输出报告 + 条件调整指令（写次日 t_conditions，publisher='ai'）
- [x] 3.4 `t_db.py`：新增 t_ai_actions 读写 helper（insert/list by trade_date/symbol）

## 4. 唤醒链升级

- [x] 4.1 `t_bridge.py`：`wake_agent` 唤醒 payload 增加 `decision_mode='ai_led'` + 上下文（触发快照/持仓摘要/最近 3 次决策/连续命中计数）；消息从"复核 auto/human"改为"决策（执行/等待/放弃/调整条件）并说明理由"
- [x] 4.2 `t_bridge.py`：`agent_review_and_execute` 保留为桥不可达降级——只标记事件待处理，不自动下单
- [x] 4.3 `t_monitor.py`：命中写 t_triggers 时计算并附 `consecutive_hits`（同条件当日连续命中计数）；达阈值（≥3 次未实质改善）时系统提示 AI 必须给出调整/冷却动作，否则条件自动进入冷却
- [x] 4.4 `t_triggers` 状态机支持 `ai_decided` / `await_retry` 中间态（pending → ai_decided → executed/blocked/cancelled/await_retry）
- [x] 4.5 单元测试：唤醒 payload 内容、连续命中计数、达阈值自动冷却

## 5. DSH Bridge 决策主体化

- [x] 5.1 `docker/dsh/bridge/lib/index.js`：做T Agent 系统提示词（TRADE_SYSTEM_PROMPT / T_BUILD_SYSTEM_PROMPT）改为决策主体视角：AI 是选股/操作/条件/复盘唯一决策者，规则只负责唤醒与风控；输出格式支持 exec/wait/abandon/update_condition
- [x] 5.2 新增/复用工具：查最近 AI 决策（t_ai_actions）、发布条件定时器（已有 create_t_condition 标注 publisher='ai' 与会话）、复盘入口工具
- [x] 5.3 更新 `/backtest/review` prompt：决策语义升级（exec/wait/abandon/update_condition），回测沙盒工具隔离不变
- [ ] 5.4 构建 dsh 镜像并部署（`docker compose up -d --build dsh` + 桥接变更生效验证 /health /chat）

## 6. 回测 AI 决策模式

- [x] 6.1 `t_backtest_runner.py` `build_review_fn`：review 响应解析扩展（exec/wait/abandon），失败降级规则模式不变
- [x] 6.2 `t_backtest.py` `TBacktestEngine._review` / `_handle_trigger`：解析 AI 决策动作（exec 才撮合，wait 记事件不撮合，abandon 记放弃；update_condition 回放中简化为当日后续冷却）
- [x] 6.3 回测事件增加决策动作维度（review 事件记录 decision 动作），metrics 统计 wait/abandon 计数
- [x] 6.4 测试：`test_t_combined_backtest.py` 增补 AI 决策模式用例（exec/wait/abandon 三态 + 降级）

## 7. 前端（可选但推荐）

- [x] 7.1 T 账户页新增"AI 决策记录"只读面板（GET /t/ai/actions?trade_date=&symbol=，展示时间/动作/理由/网关结果）
- [ ] 7.2 T 账户页条件列表标注 publisher（规则/AI）与会话
- [x] 7.3 前端构建 + 部署

## 8. 集成与部署

- [x] 8.1 全量回归：`pytest backend/tests -q`（重点 t_* 套件）
- [x] 8.2 服务器部署：compose 重建 backend/worker + dsh（bridge 变更需 `docker cp` 进 dsh-data 卷或重建）；验证 /health、/chat、TMonitor 唤醒链
- [x] 8.3 端到端验证：模拟一次条件命中 → AI 决策 → 网关执行 → t_ai_actions 审计落库；收盘复盘入口输出报告
