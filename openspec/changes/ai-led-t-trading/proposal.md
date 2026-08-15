## Why

当前做T是**规则主导**：TMonitor 按固定条件（成本×0.98 低吸等）30s 轮询触发，命中后唤醒 AI 仅做 auto/human 复核，选股、条件生成、节奏控制全部由规则代码决定。这导致（1）条件模板僵化——同一条件一天重复触发多次被 LLM 反复升级人工（实测任务 #15 中 12/18 次升级均为"条件未实质改善的重复加仓"）；（2）AI 只当"复核员"，没有机会根据盘面主动调整策略；（3）选股/建仓/操作/复盘彼此割裂，没有统一的 AI 决策主体。用户明确要求改为 **AI 主导的做T**：AI 选股、AI 操作、AI 发布条件定时器、AI 复盘——规则引擎退居为"条件唤醒 + 网关风控"。

## What Changes

- **AI 主导决策循环**：新增做T Agent（`t-agent-*` 会话）为唯一决策主体，流程为「AI 发布条件（含价格/技术指标等触发条件）→ 条件命中由 Worker 唤醒 AI → AI 自主看盘决策（查行情/持仓/指标）→ AI 决定买卖（经网关风控）→ 收盘 AI 复盘」。
- **条件即定时器**：AI 通过工具发布监控条件，条件命中即唤醒（用户选定的节奏：价格触达、MACD 死叉等事件驱动，而非固定轮询）；AI 可随时增删改条件（含冷却/复归/有效期），系统负责触发与唤醒。
- **AI 选股建仓**：候选池优先 + 全市场扫描补充（规则打分仅做初筛，最终建仓决策由 AI 依据工具返回的候选+打分+行情做出）；建仓仍走 `build_t_position`（decision_source 扩展支持 ai_led）。
- **AI 主动操作**：日常做T由 AI 全权操作——被唤醒后 AI 主动调行情/持仓/指标工具，自主决定买卖（不再局限于"命中即按建议价执行"）；所有下单仍强制过网关（熔断/STOP_ALL/涨跌停/三档资金/日上限/单票上限），AI 错误由硬风控兜底。
- **AI 复盘**：收盘后由 AI 会话复盘当日决策（触发→决策→执行→盈亏归因），输出次日条件调整建议并可直接更新条件。
- **回测对齐**：`t-backtest` LLM 复核模式升级为"AI 决策模式"（同一决策链在回放中跑，验证 AI 主导策略的历史表现）。

## Capabilities

### New Capabilities

- `t-ai-agentic`: AI 主导做T决策循环——AI 作为选股/操作/条件发布/复盘的唯一决策主体；条件命中触发 Worker 唤醒 AI 的完整链路（条件注册→命中→唤醒→决策→网关执行→复盘→条件迭代）；决策审计与风控兜底契约。

### Modified Capabilities

- `t-monitor-trigger`: 「Worker 主动唤醒 Agent」需求语义变更——从"命中后唤醒 AI 复核（auto/human 二选一）"改为"命中后唤醒 AI 决策（AI 自主看盘后决定执行/等待/放弃/调整条件）"；命中事件快照增加唤醒上下文（持仓/最近决策/连续命中计数）。
- `t-execution-risk`: 网关执行链新增 `decision_source='ai_led'` 档位（与 agent/human/daily_auto 并列）：AI 主导模式下所有下单仍走完整网关校验，但允许 AI 主动发起（含建仓后加仓/减仓/平仓），且不再要求"必须有触发事件才可下单"（AI 主动决策路径）。

## Impact

- **后端**：`backend/app/services/t_bridge.py`（唤醒→决策语义、ai_led 决策入口）；`backend/app/services/t_gateway.py`（decision_source 扩展）；`backend/app/services/t_build.py`（ai_led 建仓档）；`backend/app/services/t_db.py`（t_ai_actions 决策审计表）；`backend/app/database.py`（新增迁移）；`backend/app/services/t_ai_agent.py`（新：AI 决策循环/复盘编排）。
- **桥接**：`docker/dsh/bridge/lib/index.js`（做T Agent 系统提示词改为决策主体视角、新增 AI 决策工具：查持仓/查最近决策/发布条件定时器/复盘）。
- **前端**：T 账户页新增"AI 决策记录"与"AI 条件定时器"视图（可选）。
- **回测**：`backend/app/services/t_backtest.py` / `t_backtest_runner.py`（LLM 复核 → AI 决策模式对齐）。
- **数据/依赖**：PostgreSQL 新增 `t_ai_actions` 表；DSH bridge 会话复用现有 `t-agent-*` 会话体系。
