## Why

黄金坑评分与 DCA 定投能力已在后端完整实现（`golden_pit_service.py` / `golden_pit_dca_service.py` / `/api/v1/golden-pit/*` 10 个端点），但 Pi Agent 的聊天模式完全感知不到：服务端 `tools.ts` 没有任何黄金坑工具，前端 `ChatContainer.tsx` 虽定义了 4 个 DCA 工具却是从未注册的死代码。用户在聊天中询问"现在有黄金坑吗""DCA 定投进度如何"时，AI 只能凭空回答或引导去网页，无法基于实时数据作答。

## What Changes

- 新增 5 个只读黄金坑聊天工具并注册到服务端 `CHAT_TOOLS` 与前端 `chatTools`：`get_golden_pit_status`、`get_golden_pit_history`、`get_golden_pit_dca_status`、`get_golden_pit_dca_logs`、`get_golden_pit_etf_configs`
- 前端激活已存在但未注册的 4 个 DCA 工具（补齐 `chatTools` / `COLLAPSIBLE_TOOLS` / `TOOL_LABELS` 三处注册点）
- 新增写工具 `update_golden_pit_etf_config` 并仅注册到 trade 模式（`TRADE_TOOLS`），不进入只读聊天模式
- 服务端 `tools.ts` 与前端 `ChatContainer.tsx` 同步实现同名同参数工具，保持两套代码一致
- 更新聊天系统提示词（`index.ts` 内嵌 `CHAT_SYSTEM_PROMPT` 与后端 `prompt_seeds.py`），说明黄金坑工具的使用时机，避免 AI 不知道何时调用
- 不开放 `execute_golden_pit_dca`（真实下单）给 LLM，维持调度器定时触发现状

## Capabilities

### New Capabilities
- `golden-pit-chat-tools`: 将黄金坑评分、历史、DCA 状态/日志/配置封装为 Pi Agent 聊天工具，覆盖服务端 `tools.ts`、前端 `ChatContainer.tsx` 与系统提示词的注册与使用约定

### Modified Capabilities
<!-- 无既有 spec 的需求变更：agent spec 仅约束聊天端点/会话/专家组，工具注册细节属于新能力 -->

## Impact

- `servers/pi-server/src/tools.ts`: 新增 6 个工具定义；`CHAT_TOOLS` 加 5 个只读工具，`TRADE_TOOLS` 加 1 个写工具
- `servers/pi-server/src/index.ts`: `chatTools = CHAT_TOOLS.map(toAgentTool)` 自动生效，无需改逻辑；更新 `CHAT_SYSTEM_PROMPT`（189 行）工具使用说明
- `frontend/src/components/ChatContainer.tsx`: 复用 1286-1455 行已有工具定义，补齐 `chatTools`（1757 行）、`COLLAPSIBLE_TOOLS`（1809 行）、`TOOL_LABELS`（1822 行）三处注册；新增 `get_golden_pit_status` / `get_golden_pit_history`
- `backend/app/db/prompt_seeds.py`: 同步 `CHAT_SYSTEM_PROMPT` 种子，保持 API 下发提示词与内嵌回退一致
- 后端 API 无改动（全部复用现有 `/api/v1/golden-pit/*` 端点）；不涉及数据库 schema 变更
