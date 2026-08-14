## Purpose

DSH 容器对外提供的 HTTP 桥接层——以 Pi Server 兼容的端点契约（`POST /chat`、`POST /chat/stream`、`GET /health`、`POST /reset`）承接 QQ Bot、前端 reflect 模式等程序化消费者，将请求映射为 DSH Agent 会话并返回回复。

## ADDED Requirements

### Requirement: 聊天端点
DSH 容器 SHALL 提供 `POST /chat` 端点，接收 `{message, session_id, mode, model, thinking_level}`，返回 `{reply, session_id, mode, elapsed_ms}`；`mode` 支持 `chat`（只读）与 `trade`（含写工具），不支持的 mode SHALL 返回 400。

#### Scenario: 发送消息
- **WHEN** 调用 `POST /chat`，携带 `message` 与 `session_id`
- **THEN** DSH Agent 处理消息并返回 `reply`、`session_id`、`elapsed_ms`

#### Scenario: 缺少 message
- **WHEN** 请求体不含 `message` 或为空
- **THEN** 返回 400 与错误信息

#### Scenario: 会话续接
- **WHEN** 使用已存在的 `session_id` 再次调用
- **THEN** 会话历史被保留，新消息在其上下文中处理

#### Scenario: 模型覆盖
- **WHEN** 请求携带 `model` 或 `thinking_level`
- **THEN** 本次会话使用指定的模型/思考档位（未指定时使用会话默认值）

### Requirement: SSE 专家组流端点
DSH 容器 SHALL 提供 `POST /chat/stream` 端点，接收 `{message, session_id, skip_data_collection, panel_mode}`，以 SSE 事件流推送专家组讨论的阶段事件（`start` / `expert_message` / `done` / `error`），并在 `done` 事件携带最终报告。

#### Scenario: 流式专家组讨论
- **WHEN** 调用 `POST /chat/stream` 携带 `message`
- **THEN** 依次收到 `start` 事件、各专家阶段的 `expert_message` 事件、携带最终 `reply` 的 `done` 事件

#### Scenario: 讨论失败
- **WHEN** 专家组讨论过程中发生错误
- **THEN** 推送 `error` 事件并结束流

### Requirement: 健康检查
DSH 容器 SHALL 提供 `GET /health` 端点，返回服务状态与存活会话数。

#### Scenario: 健康检查
- **WHEN** 调用 `GET /health`
- **THEN** 返回 `{status: "ok", sessions: <数量>}`

### Requirement: 会话重置
DSH 容器 SHALL 提供 `POST /reset` 端点，接收 `{session_id, mode}`，删除指定会话的内存状态与持久化记录。

#### Scenario: 重置会话
- **WHEN** 调用 `POST /reset` 携带 `session_id`
- **THEN** 该会话状态被清除，后续使用同一 `session_id` 从头开始

### Requirement: 会话并发锁
同一 `session_id` 的并发请求 SHALL 串行处理——后到的请求等待前一个完成，避免同一 Agent 并发驱动。

#### Scenario: 并发请求串行化
- **WHEN** 同一 `session_id` 的两个请求几乎同时到达
- **THEN** 第二个请求等待第一个完成后才被处理，且两个请求都返回各自正确回复

### Requirement: 会话持久化
会话消息 SHALL 在进程重启后仍可恢复，恢复时 SHALL 清理无效历史（如孤立 tool 消息），保证恢复的会话可正常发送。

#### Scenario: 重启恢复
- **WHEN** DSH 容器重启后使用旧 `session_id` 调用
- **THEN** 会话历史被加载，对话可继续

### Requirement: 回测模式不再支持
DSH 容器 SHALL NOT 支持回测模式：`[BKT:...]` 前缀与回测沙盒工具路由 MUST NOT 存在；收到 `mode=backtest` SHALL 返回 400。

#### Scenario: 回测请求被拒
- **WHEN** 调用 `POST /chat` 携带 `mode=backtest`
- **THEN** 返回 400 与"回测模式已下架"错误

### Requirement: 服务发现与配置
DSH 容器端点 SHALL 通过环境变量配置（端口、Backend API 地址、DeepSeek/MiniMax API Key），并在启动时从 Backend 加载系统 Prompt（失败时回退内置）。

#### Scenario: 启动加载 Prompt
- **WHEN** DSH 容器启动
- **THEN** 从 Backend Prompt API 拉取启用的 prompt 并缓存，失败时使用内置回退并告警
