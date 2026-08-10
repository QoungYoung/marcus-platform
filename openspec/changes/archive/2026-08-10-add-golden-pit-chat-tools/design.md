## Context

黄金坑评分（`golden_pit_service.py`）与 DCA 定投（`golden_pit_dca_service.py`）已通过 `backend/app/api/golden_pit.py` 暴露 10 个 HTTP 端点（`/api/v1/golden-pit/*`）。Pi Agent 的聊天模式由两层独立的工具系统组成，二者各自维护一份工具定义：

- 服务端 `servers/pi-server/src/tools.ts`：`CHAT_TOOLS`（只读）/ `TRADE_TOOLS`（含下单）/ `REFLECT_TOOLS`，经 `index.ts` 的 `toAgentTool()` 注入 LLM
- 前端 `frontend/src/components/ChatContainer.tsx`：`chatTools` 数组 + `COLLAPSIBLE_TOOLS`（折叠渲染）+ `TOOL_LABELS`（中文名映射），经 `createTool()` 注册

现状缺口：`tools.ts` 无任何黄金坑工具；`ChatContainer.tsx:1286-1455` 已定义 4 个 DCA 工具但从未注册（死代码）。系统提示词 `CHAT_SYSTEM_PROMPT`（`index.ts:189` 内嵌 + 后端 `db/prompt_seeds.py` 种子）也没有黄金坑工具的调用指引。

约束：聊天模式语义为"只读"（`tools.ts` 注释），写操作工具只允许出现在 trade 模式；后端 API 与数据库不可改动（本变更全部复用现有端点）。

## Goals / Non-Goals

**Goals:**
- 让服务端与前端聊天模式的 AI 都能调用黄金坑只读查询（状态/历史/DCA 状态/DCA 日志/ETF 配置）
- 激活前端已存在的 4 个 DCA 工具定义，补齐注册点
- 将 `update_golden_pit_etf_config` 作为写工具仅注册到 trade 模式
- 同步系统提示词，让 AI 知道何时使用这些工具
- 服务端与前端工具定义保持同名、同参数、同输出格式

**Non-Goals:**
- 不新增/修改后端 API 端点（`execute_golden_pit_dca` 不开放给 LLM）
- 不改动数据库 schema
- 不重构现有双份工具代码的架构（合并为单一来源超出本变更范围）
- 不做黄金坑晨报工具（`status.summary` 已含 v2 总结文本，后续按需再议）

## Decisions

**D1: 工具清单与模式归属**
- chat（只读）：`get_golden_pit_status`、`get_golden_pit_history`、`get_golden_pit_dca_status`、`get_golden_pit_dca_logs`、`get_golden_pit_etf_configs`
- trade（写）：`update_golden_pit_etf_config`
- 理由：与 `CHAT_TOOLS` 只读语义一致；`update_golden_pit_etf_config` 会改变后续自动定投金额/策略，放 trade 模式由交易决策流程使用
- 备选：全部进 chat → 违反只读约定，风险不可接受

**D2: 双端同步实现**
- `tools.ts` 与 `ChatContainer.tsx` 各自定义同名工具对象（`name`/`label`/`description`/`parameters`/`execute`），服务端用 `apiFetch`（统一错误处理），前端用相对路径 `MARCUS_API = '/api/v1'` fetch，响应统一为 `{ content: [{ type: 'text', text: markdown }], details }`
- 理由：`tools.ts` 本就是"从 ChatContainer 提取的服务端版本"，沿用既有双份模式，改动最小
- 备选：抽象共享工具模块 → 引入跨包依赖与构建链路改动，超出本变更范围

**D3: `get_golden_pit_status` 输出裁剪**
- markdown 文本只输出 pit/warning 状态指数明细 + 窗口 + 三重确认 + 预测 + 宏观摘要；`details` 保留全量 indices
- 理由：`/golden-pit/status` 返回全 tier 指数（含 watch/drop 共几十行），直接透传会浪费 LLM 上下文
- 备选：原样输出 → token 开销大且噪音多

**D4: 写工具的安全边界**
- `update_golden_pit_etf_config` 仅进 `TRADE_TOOLS`；`execute_golden_pit_dca` 完全不注册（真实下单，继续由调度器 `scheduler_service.py` 定时触发）
- 理由：LLM 误触发真实买入风险不可接受；trade 模式已含下单工具，有既有决策护栏

**D5: 提示词同步**
- `CHAT_SYSTEM_PROMPT` 同时在 `index.ts` 内嵌回退与后端 `prompt_seeds.py` 种子中追加"黄金坑工具使用时机"小节
- 理由：`getPrompt()` 优先取 API 缓存（`fetchPromptsFromAPI`），仅改一处会导致两套提示词不一致

**D6: 前端注册三件套**
- 新工具必须同时注册进 `chatTools`（1757 行）、`COLLAPSIBLE_TOOLS`（1809 行）、`TOOL_LABELS`（1822 行）；否则不渲染中文名/折叠视图
- 理由：`TOOL_LABELS[toolName] || toolName` 兜底会退化为英文名，`COLLAPSIBLE_TOOLS` 缺失则工具结果不可折叠

## Risks / Trade-offs

- [双份代码漂移：`tools.ts` 与 `ChatContainer.tsx` 各自维护，改参数/描述时易漏一端] → 任务中增加"双端同名同参"核对步骤；工具名与参数表在 tasks 中显式列出
- [`get_golden_pit_status` 首调慢：`dca/status` 内部调 `get_status()` 走 ArkVol 拉取（2h TTL 缓存）] → execute 内处理超时与 `code !== 0`；描述中说明数据为日级快照
- [写工具被 LLM 在聊天中误用] → 只进 `TRADE_TOOLS`；description 明示"修改后影响后续自动定投，需确认"
- [提示词漂移：API 种子与内嵌回退不一致] → 同一任务内双处同步更新，并在设计评审时核对

## Migration Plan

- 无后端/数据库迁移。部署顺序：后端种子更新（`prompt_seeds.py`）→ 服务端 `tools.ts`/`index.ts` → 前端 `ChatContainer.tsx`；服务端与前端改动独立可回滚（删除工具定义/注册即可）
