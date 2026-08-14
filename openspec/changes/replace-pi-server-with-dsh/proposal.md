## Why

Pi Server（`servers/pi-server/`，Node.js + TypeScript，端口 3001）是独立维护的 Agent 桥接服务：依赖外部 `@earendil-works/pi-agent-core` 运行时，专家组群聊编排硬编码在 1800+ 行 `index.ts` 中（角色、轮次、prompt 全写死），回测上下文（`[BKT:]` 前缀 + AsyncLocalStorage）与工具沙盒路由深度耦合，改一个角色或加一个工具都要动核心编排函数。而 DeepSeek Harness（DSH）的 LLM 层（`dsh-llm-pi-ai`）本就构建在 Pi Server 所用的同一个 `@earendil-works/pi-ai` 之上——模型行为、thinking 档位、DeepSeek v4 系列完全同源，且 DSH 自带声明式的多智能体编排（AgentTeams）、原生工具注册与健壮的会话持久化。用 DSH 替代服务端 Pi Server，可以在不改变模型行为的前提下，把"改编排靠改代码"变成"改编排靠改配置/加成员"。

## What Changes

- **BREAKING** 移除 `servers/pi-server/`（Node.js 桥接服务）及其在 `marcus.bat`、`docker-compose.yml`、`docker/nginx.conf` 中的启动/代理条目。
- **BREAKING** 下架回测引擎对 Pi Server 的依赖：`backtest_engine.py` 的 `_call_pi_server` / `_build_full_prompt`、`BACKTEST_ONLY_TOOLS`、`[BKT:]` 上下文解析与工具沙盒路由一并移除（回测引擎整体停用，后续另行恢复）。
- 新增 DSH 容器（Docker 镜像，替代 piserver 容器）：运行自定义 DSH profile，暴露 Pi Server 兼容的 HTTP 端点。
- 新增 `dsh-marcus-bridge` 插件（DSH 原生插件）：提供 `POST /chat`（JSON 同步）、`POST /chat/stream`（SSE 专家组流）、`GET /health`、`POST /reset`，实现 `session_id` → DSH Agent 的映射与 per-session 锁。
- 专家组群聊（reflect 模式）重构为 **AgentTeams** 编排：持久化成员（风控审计师/趋势交易员/数据统计师/逆向质疑者/主持人）、任务依赖图（采集→独立分析→交叉评论→反思改进→主持人综合）、成员间直连消息，替代 `executePanelDiscussion` 硬编码 4 轮。
- 交易写工具注册为 **DSH 原生 tool**（强参数校验，供 chat/trade/panel 模式共用）：`place_order`、`cancel_order`、`calc_position`、`update_golden_pit_etf_config` 等；只读查询工具保持 skill 形态（`marcus-panel-tools` 已覆盖 21 个 reflect 只读工具）。
- QQ Bot（`qqbot_service.py`）保留，`_call_pi_server` 改为调用 DSH 容器端点（API 契约对齐：`{message, session_id, mode} → {reply, session_id}`）。
- 前端：chat 模式**保留**浏览器端 pi-agent-core（不做改动）；reflect 模式 SSE 转发目标从 piserver 容器改为 DSH 容器（`/panel` nginx 代理改指）。

## Capabilities

### New Capabilities
- `dsh-chat-bridge`: DSH 容器对外 HTTP 桥接——Pi Server 兼容的 `POST /chat`、`POST /chat/stream`（SSE）、`GET /health`、`POST /reset`，会话映射与并发锁、模型/思考档位覆盖。
- `agent-teams-panel`: reflect 专家组群聊的 AgentTeams 编排——成员角色、任务依赖流程、成员直连消息、最终报告产出与 SSE 事件推送。
- `dsh-native-trade-tools`: 交易写工具作为 DSH 原生 tool 注册——`place_order` / `cancel_order` / `calc_position` / `update_golden_pit_etf_config` 等强校验工具，及与只读 skill 的边界。

### Modified Capabilities
- `agent`: Expert Panel（Reflect Mode）的编排方从 Pi Server 改为 AgentTeams；服务端 chat 通道（QQ Bot 消费）改走 DSH 容器。
- `dual-track-sector-selection`: `get_concept_fund_flow_5d` 工具的注册方从 `pi-server tools.ts` 改为 DSH 原生工具注册。
- `golden-pit-chat-tools`: 服务端工具注册从 `tools.ts`（CHAT_TOOLS/TRADE_TOOLS）改为 DSH 工具注册；前端 `ChatContainer.tsx` 的只读工具注册保留不变。

## Impact

- **删除**：`servers/pi-server/`（`index.ts` / `tools.ts` / `package.json` / `sessions/`）；`marcus.bat` 中的 Pi Server 条目；`docker/Dockerfile.piserver`、`docker-compose.yml` 的 `piserver` 服务、`docker/nginx.conf` 的 `/panel` 与 `/chat/stream` 代理目标。
- **修改**：`backend/app/services/qqbot_service.py`（端点 URL + 契约适配）；`backend/app/api/panel.py`（SSE 代理目标）；`backend/app/services/backtest_engine.py`（下架 Pi 调用）；`backend/app/config.py`（`PI_SERVER_URL` 语义改为 DSH 端点）；`frontend/src/components/ChatContainer.tsx`（reflect 转发目标，其余不动）；`docker/docker-compose.yml`（新增 dsh 服务）。
- **依赖**：服务端移除 `@earendil-works/pi-agent-core` / `pi-ai` / `pi-web-ui`；前端保留（chat 模式继续使用）。新增 DSH 运行时（Docker 内，含 `dsh-llm-pi-ai` 等组合包）与 `dsh-marcus-bridge` 插件。
- **系统**：Docker Compose 服务由 `postgres + backend + worker + piserver + frontend` 变为 `postgres + backend + worker + dsh + frontend`（数量不变，piserver 替换为 dsh）。
- **评估项**：`packages/trading-agent/`（基于 pi-agent-core 的独立包，未被 import，仅 CSS 选择器字符串引用）——不阻塞本变更，可后续单独清理。
