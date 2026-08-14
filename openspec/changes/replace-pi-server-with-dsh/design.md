## Context

现状见 proposal.md — Why。与本设计直接相关的约束：

- Pi Server（`servers/pi-server/`，Node 3001）是唯一被程序化消费的 Agent 桥：QQ Bot（`qqbot_service.py`）、前端 reflect 模式（经 `panel.py` + nginx `/panel` 代理）、回测引擎（`backtest_engine.py`）都通过 `POST /chat` / `POST /chat/stream` 调用它。
- DSH 的 LLM 适配器 `dsh-llm-pi-ai` 构建在 Pi Server 所用的同一个 `@earendil-works/pi-ai` 之上——模型路由、DeepSeek v4 档位、thinking level、KV cache 回放同源，迁移不存在模型行为断层。
- DSH 已在本机运行（Web GUI :3080），提供 `ctx.agents.create/resume/followup/whenIdle`（Agent 会话原语）与 `ctx.webServer.register(route)`（HTTP 路由注册），以及 AgentTeams（多成员任务编排）。
- `marcus-panel-tools` skill 已从 Pi Server `REFLECT_TOOLS` 移植 21 个只读工具（HTTP API 描述形态）。
- DSH 组合包分层：`dsh-base`（核心：模型/工具/持久化/settings）之上叠加表层（`dsh-web-app` = GUI、`dsh-headless` = 一次性任务）。**没有任何现成表层是"长驻 HTTP 服务"**——需自建 profile。

## Goals / Non-Goals

**Goals:**
- 用 Docker 中的 DSH 容器提供 Pi Server 兼容的 HTTP 契约，QQ Bot 与前端 reflect 模式零体验变化切换。
- 专家组群聊从硬编码 4 轮改为 AgentTeams 声明式编排，角色/任务可配置。
- 交易写工具以 DSH 原生 tool 注册（强校验），只读工具保持 skill。
- 移除回测引擎对 Pi 的依赖并停用回测。
- 最终删除 `servers/pi-server/`。

**Non-Goals:**
- 不改前端 chat 模式（保留浏览器端 pi-agent-core）。
- 不迁移 `packages/trading-agent/`（未被 import，后续单独处理）。
- 不重建回测引擎（下架，未来另行提案）。
- 不改变现有 `tools.ts` 工具命名/参数契约（迁移保持兼容）。

## Decisions

### D1: 自建"服务型" DSH profile（而非 web-app / headless）
`dsh-web-app` 带 GUI 且其 CLI 拒绝 `--host 0.0.0.0`；`dsh-headless` 是一次性任务进程（跑完退出），都不适合长驻容器服务。**决策**：自建 profile = `dsh-base` + `dsh-host-webserver` + `dsh-marcus-bridge`（自定义插件）+ AgentTeams 插件，webserver 配置 `host: 0.0.0.0`（绕过 web-app 的 CLI 限制，profile 组合层直接配置）。
- 备选：复用 `dsh-headless` 每次起进程 → 每次会话重建、无 HTTP 长驻、无法做 SSE，否决。
- 备选：复用 `dsh-web-app` 并 patch 掉 0.0.0.0 拒绝 → 携带无关 GUI 依赖（React dist、HMR），容器体积与复杂度上升，否决。

### D2: `dsh-marcus-bridge` 插件负责 HTTP 面与会话映射
插件（host 半，`lib/index.js`）注册路由：`POST /chat`、`POST /chat/stream`（SSE）、`GET /health`、`POST /reset`。内部维护 `Map<session_id, {agent, lock}>`：
- 首次请求 → `ctx.agents.create({systemPrompt, model, tools})`；后续 → 复用同一 agent（DSH 自带 jsonl 持久化，重启可 `resume`）。
- 并发：per-session promise 链（对齐 index.ts 的 `locks`）。
- `POST /chat/stream` → 触发 AgentTeams 专家组 → 订阅成员事件 → SSE 推送。
- 模型覆盖 `{model, thinking_level}` 透传给 `create` 的模型路由；`mode=backtest` 直接 400。
- Prompt 启动时从 Backend `/prompts` 拉取缓存，失败回退内置（对齐现行为）。

### D3: 专家组用 AgentTeams 编排，bridge 桥接 SSE
`agent-teams-panel` 流程映射为 AgentTeams：`agent_teams_create`（一次讨论一个团队）→ `add_member`（风控/趋势/数据/逆向/主持人，各自模型配置）→ `create_task` + 依赖（采集 → N×独立分析 → N×交叉评论 → N×反思 → 主持人综合）→ `send_message` 下发任务。成员产出经 mailbox 到达，bridge 插件把每份产出转成 `expert_message` SSE 事件（对齐前端现有气泡体验）。
- 备选：workflow 脚本（`subagent` 并行 + 顺序阶段）→ 一次性执行、无持久成员、成员间无直连消息，比 AgentTeams 少"群聊"语义，但实现更简单。选 AgentTeams 因用户明确选 C 且成员可复用。
- SSE 事件结构与现状一致：`start` / `expert_message {phase,label,results[],elapsed_sec}` / `done {reply,elapsed_ms}` / `error`。

### D4: 写工具 → DSH 原生 tool；只读工具 → skill 保持
写工具（`place_order`、`cancel_order`、`calc_position`、`update_golden_pit_etf_config`）以 DSH tool 插件注册：JSON Schema 参数校验、执行时 `fetch` Backend API（MARCUS_API_URL 环境变量），tool 名称/参数/端点与 `tools.ts` 完全一致（契约兼容）。只读工具不重复注册，沿用 `marcus-panel-tools` skill（DSH 容器需把该 skill 装入 profile 的 skills 目录）。
- 备选：全部工具注册为原生 tool → 40+ 工具重复实现，与既有 skill 重叠，工作量翻倍，否决。
- 备选：全部保持 skill → 写工具靠模型拼 HTTP 请求，参数校验弱、下单类风险高，否决。

### D5: 消费方契约适配而非重构
- `qqbot_service._call_pi_server`：URL 改指 DSH 容器（`PI_SERVER_URL` 环境变量语义不变，值改为 `http://dsh:3001/chat`），请求/响应 JSON 结构保持不变（`{message,session_id,mode}` → `{reply,session_id}`）。
- `panel.py`：SSE 代理目标改指 `http://dsh:3001/chat/stream`。
- nginx `/panel` 与 `/chat/stream` 代理上游 `piserver:3001` → `dsh:3001`。
- 前端 `ChatContainer.tsx`：仅 reflect 模式的转发目标感知（走 `/panel` 代理，URL 不变则前端零改动；若直连则改 URL）。
- `backtest_engine.py`：删除 `_call_pi_server` / `_build_full_prompt` / 回测调度入口（回测下架）。

### D6: 回测依赖整体移除
`BACKTEST_ONLY_TOOLS`、`[BKT:]` 前缀解析、工具层 `AsyncLocalStorage` 回测上下文、`/reports/{task_id}` 端点（前端 BacktestPage 的"下载 Pi 报告"随之失效并移除）一并删除，不保留兼容分支（回测另行恢复时重新设计）。

## Risks / Trade-offs

- [DSH 容器内 0.0.0.0 绑定与 Linux 工具栈（bash 沙盒）未经验证] → 先做 spike（任务 1）：最小 profile 容器化 + 健康检查 + 一次 agent 回合跑通再铺开。
- [AgentTeams 成员事件如何被 bridge 订阅（插件内部事件 vs 轮询 mailbox）未定] → 设计上以"成员产出即事件"为接口，实现时优先插件事件订阅，兜底轮询 `agent_teams_status`；此细节不影响 specs 与任务拆分。
- [SSE 长连接在 nginx 后的缓冲] → 沿用现有 `X-Accel-Buffering: no` 头（nginx.conf 已有），新端点同样设置。
- [切换期间 QQ Bot / 前端不可用] → 双跑策略：新 DSH 容器与旧 piserver 并存，先切 QQ Bot 灰度，稳定后再删 pi-server（迁移计划）。
- [Prompt 双源（Backend /prompts + DSH 内置）漂移] → 沿用现有"启动拉取 + 回退"模型，prompt_seeds.py 仍是唯一真源；bridge 不内置第二份 prompt 真源。
- [写工具从 pi-agent-core AgentTool 换成 DSH 原生 tool 的调用语义差异] → D1/D2 中 bridge 的 tool 注册直接复用 `tools.ts` 参数契约，且写工具数少（≤6），逐个对齐测试。

## Migration Plan

1. **Spike**：DSH 容器最小 profile（base + webserver，`host: 0.0.0.0`）+ 健康检查 + `POST /chat` 一次回合跑通；验证 Linux bash 工具栈与 DeepSeek 路由。
2. **开发**：`dsh-marcus-bridge` 插件（本地 DSH 先跑通 `/chat` → 容器化）。
3. **专家组**：AgentTeams 编排 + SSE 桥接，本地联调前端 Panel 体验。
4. **写工具**：DSH 原生 tool 注册 + 契约对齐测试。
5. **双跑切换**：compose 新增 `dsh` 服务（旧 piserver 保留），nginx 上游切换为 dsh → QQ Bot + 前端 reflect 验证 → 稳定后删 piserver 服务与代码。
6. **下架回测**：删除 backtest_engine 的 Pi 调用、`BACKTEST_ONLY_TOOLS`、`[BKT:]` 逻辑、`/reports` 端点与前端下载入口。
7. **清理**：删 `servers/pi-server/`、`docker/Dockerfile.piserver`、marcus.bat 条目、`@earendil-works/*` 服务端依赖。

**回滚**：git 历史保留 pi-server 全量；切换阶段如 DSH 容器异常，nginx 上游改回 `piserver:3001` 即可（双跑期间旧服务仍可用）。

## Open Questions

- AgentTeams 讨论的成员消息订阅机制（插件事件 vs mailbox 轮询）——实现细节，不改变 specs/任务。
- DSH 容器镜像构建方式（`pnpm dlx dsh` 安装 vs 固定版本 vendored）——spike 中决定。
- reflect 报告持久化格式（沿用 `sessions/panel_*.json` 还是 DSH 会话目录）——不影响对外契约。
