## Context

现状（见 proposal.md - Why）：做T为规则主导——TMonitor 30s 轮询规则条件（成本×0.98 低吸等）触发，命中后唤醒 DSH 做T Agent 仅做 auto/human 复核，选股/条件生成/节奏全部由规则代码决定。已具备的基础设施：DSH bridge（`docker/dsh/bridge/lib/index.js`）提供 `/chat`（mode=chat/trade/backtest）与做T工具集（place_order/list_t_fields/create_t_condition/scan_t_candidates/build_t_position 等）；网关三权分立（Worker 事件发生器 / Agent 决策 / 网关唯一放行）；`t_conditions`/`t_triggers` 状态机；`build_t_position` 支持 decision_source（agent/human/daily_auto）。

目标形态：**规则唤醒 + AI 主导 + 网关风控**——AI 发布条件（价格/技术指标）作为唤醒定时器，条件命中由 TMonitor 唤醒 AI 会话，AI 自主看盘决策买卖（含主动建仓后加仓/减仓），网关兜底风控，收盘 AI 复盘并迭代条件。

## Goals / Non-Goals

**Goals:**
- 建立「AI 条件发布 → 命中唤醒 → AI 决策 → 网关执行 → 审计 → 复盘迭代」的完整 Agentic 闭环
- AI 成为选股/操作/条件/复盘唯一决策主体；规则仅承担命中检测、唤醒、风控、审计
- 全部下单强制过网关（熔断/STOP_ALL/涨跌停/三档资金/日上限/单票上限），ai_led 不豁免
- 决策全量审计（t_ai_actions），支撑复盘与回测对照
- 回测 LLM 复核模式升级为同一 AI 决策链，历史验证 AI 主导策略

**Non-Goals:**
- 不改动 stock/golden_pit 主账户交易行为
- 不实现真实盘口/滑点模拟（沿用 simulated slippage estimate）
- 不做 AI 自主的全市场逐股遍历（选股仍以候选池/扫描短名单为界，见用户决策）
- 不引入新 LLM 供应商（复用 DSH bridge + DeepSeek 网关）

## Decisions

### D1. 唤醒链：条件命中 → Worker POST /chat（mode=trade）唤醒做T Agent
沿用现有 `t_bridge.wake_agent` 通道，但语义升级：唤醒 payload 增加 `decision_mode=ai_led` 与完整上下文（触发快照、当前持仓摘要、该条件最近 N 次决策、连续命中计数）。Agent 会话（`t-agent-{symbol}`）以决策主体系统提示词运行，可调用现有工具（查行情/持仓/指标）后输出决策。
- **备选**：新增专用 `/t/ai/review` 端点。否决——复用 `/chat` 的会话持久化与工具隔离机制，避免双通道。
- **关键改动**：`t_bridge.wake_agent` 的唤醒消息从"复核并决定 auto/human"改为"决策并说明理由（执行/等待/放弃/调整条件）"；`agent_review_and_execute` 保留为规则兜底（桥不可达时只标记不自动下单）。

### D2. AI 决策入口：新增 `t_ai_agent.py` 编排 + `t_ai_actions` 审计表
新增服务模块 `backend/app/services/t_ai_agent.py`：
- `handle_ai_decision(trigger, context)`：被唤醒后由 AI 输出决策，解析为结构化 action（exec/wait/abandon/update_condition），exec 走 `gateway_execute(decision_source='ai_led')`，update_condition 走条件更新；每条决策写 `t_ai_actions`。
- `ai_daily_review()`：收盘复盘入口，拉当日决策链 → 唤醒 AI 复盘会话 → 输出复盘报告 + 条件调整指令。
- `ai_select_and_build()`：AI 选股建仓入口（候选池优先，scan 补充），决策经 `build_t_position(decision_source='ai_led')`。
- **表**：`t_ai_actions(id, session_id, trade_date, symbol, action_type, input_snapshot JSONB, output JSONB, gateway_result JSONB, created_at)`。
- **备选**：把编排塞进 t_bridge.py。否决——bridge 已 794 行，职责应分离。

### D3. 网关：decision_source 扩展 'ai_led'
`t_gateway.py` 的 `validate_order_at` / `build_gateway_execute` 增加 `decision_source='ai_led'`：
- 与 agent 同档风控（不豁免任何校验）；`allow_first_open` 语义保持（建仓首开仍需人工确认，除非 ai_led 显式场景下由网关决定——沿用 daily_auto 的先例：建仓走 build_t_position 而非 place_order）。
- 主动买卖（无触发事件）走 `gateway_execute(symbol, side, price, volume, decision_source='ai_led', reason=...)`，仍受可卖底仓/三档/日上限约束。
- **理由**：用户明确"保留全部网关风控"；ai_led 只改变"谁发起"，不改变"谁放行"。

### D4. 条件即定时器：t_conditions 扩展发布者与会话字段
`t_conditions` 增加 `publisher VARCHAR(16) DEFAULT 'rule'`（rule/ai）与 `session_id VARCHAR(64)`（发布该条件的 AI 会话），用于（a）唤醒时定位会话；（b）AI 增删改自己的条件；（c）复盘时按发布者归因。命中后状态机增加 `ai_decided` / `await_retry` 中间态（见 specs t-monitor-trigger delta），冷却/复归逻辑复用现有滞回状态机。

### D5. 连续命中防护：唤醒上下文携带命中计数
TMonitor 写 t_triggers 时计算该条件当日连续命中数（同条件相邻命中无实质间隔计数），唤醒上下文附 `consecutive_hits` 与最近 3 次决策摘要；达阈值（如 ≥3 次未实质改善）时系统提示 AI 必须给出"调整或冷却条件"的明确动作，否则该条件自动进入冷却（防 LLM 反复判 same 情形，对应实测 #15 的问题）。

### D6. 回测对齐：LLM 复核模式升级为 AI 决策模式
`t_backtest_runner.build_review_fn` 的 review 语义从"复核 auto/human"升级为"决策"：POST /backtest/review 的 prompt 允许 AI 输出 exec/wait/abandon/update_condition（回放中 update_condition 简化为"本条件本日后续触发按冷却处理"）；`TBacktestEngine._review` 解析扩展。回测仍用独立沙盒会话（`t-backtest-{taskId}`，工具 restrict deny 生产写工具）。
- **注意**：不改变现有回测的规则模式；仅 llm 模式的决策语义升级。

### D7. 复盘：复用专家组/单 Agent 会话，不做新面板
收盘复盘由 `ai_daily_review()` 唤醒 `t-agent-*` 会话（mode=trade 或独立复盘 prompt），拉当日 `t_ai_actions` 生成报告；前端 T 账户页新增只读"AI 决策记录"面板（可选任务，非必须）。

## Risks / Trade-offs

- [LLM 幻觉下单] → 网关硬风控兜底（熔断/STOP_ALL/三档/日上限/单票上限），ai_led 不豁免；决策审计可追责。
- [唤醒延迟（LLM 响应 5-15s）导致错失最优价] → 触发即写事件 + 网关二段实时断言（价格偏离方向则 blocked），与现有复核链延迟特性一致；条件触发价带滞回带吸收延迟。
- [AI 反复触发同条件造成事件风暴] → 连续命中计数 + 达阈值强制冷却（D5）。
- [桥不可达时 AI 决策不可用] → 降级轮询只标记事件待处理，绝不自动下单（spec 强制）。
- [回测决策链与实盘差异] → 回测用同一网关校验函数（state-injected），caliber_notes 明示"AI 决策模式"口径。
- [t_ai_actions 增长] → 按 trade_date 分区/定期清理策略（保留 90 天），后续任务。

## Migration Plan

1. 后端迁移：`database.py` 新增 `_apply_ai_led_migration`（t_ai_actions 表 + t_conditions publisher/session_id 列，幂等）。
2. 后端服务：新增 `t_ai_agent.py`；`t_gateway.py` 扩展 ai_led；`t_bridge.py` 唤醒语义升级；`t_monitor.py` 命中上下文扩展。
3. Bridge：`docker/dsh/bridge/lib/index.js` 做T Agent 系统提示词改为决策主体 + 复盘 prompt；工具集基本复用，新增"查最近决策"工具（可选）。
4. 回测：`t_backtest_runner.py` / `t_backtest.py` LLM 决策模式升级。
5. 前端（可选）：T 账户页 AI 决策记录面板。
6. 测试：单元测试（网关 ai_led、审计落库、连续命中冷却、回测 AI 决策模式）+ 集成回归。
7. 部署：compose 重建 backend/worker + dsh 容器（bridge 变更需重建 dsh 并 docker cp 进 dsh-data 卷）；dsh 无改动则跳过。

## Open Questions

- ai_led 建仓"首开 B1"是否仍需人工确认：初版沿用 daily_auto 先例（自动放行，但受时段/规模/熔断全链约束），如需收紧可在后续敏感度调整。此决策不改变 spec（spec 允许 ai_led 声明放开）。
- 复盘触发时刻：默认 15:05（复用 TBuildService 盘后窗口）还是独立调度；初版并入 15:05 窗口，避免新增调度面。
