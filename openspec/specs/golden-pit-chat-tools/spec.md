# golden-pit-chat-tools Specification

## Purpose
Pi Agent 聊天/交易模式的黄金坑工具封装规范 — 定义黄金坑评分、历史、DCA 状态/日志/ETF 配置如何暴露为可调用工具，以及写工具的模式边界。

## Requirements

### Requirement: 黄金坑状态聊天工具
Pi Agent 聊天模式 SHALL 提供 `get_golden_pit_status` 工具，调用 `GET /api/v1/golden-pit/status` 获取逐指数黄金坑状态、窗口、三重确认、预测与宏观摘要，并将文本输出裁剪为 pit/warning 状态指数明细 + 窗口信息 + 确认摘要。

#### Scenario: 查询黄金坑总览
- **WHEN** 用户在聊天中询问"现在有哪些指数在黄金坑"
- **THEN** AI 调用 `get_golden_pit_status` 并基于返回的指数状态、窗口与三重确认信息作答

#### Scenario: 状态接口失败
- **WHEN** `/api/v1/golden-pit/status` 返回非零 `code` 或网络错误
- **THEN** 工具抛出带 `msg` 的错误信息，AI 明确告知用户暂时无法获取黄金坑状态

### Requirement: 黄金坑历史工具
Pi Agent 聊天模式 SHALL 提供 `get_golden_pit_history` 工具，参数为 `index`（基金代码，默认 all）与 `days`（1-2000，默认 60），调用 `GET /api/v1/golden-pit/history` 返回贪婪值历史走势。

#### Scenario: 查询单指数历史
- **WHEN** 用户询问"某指数贪婪值近期走势"
- **THEN** AI 以该指数基金代码调用 `get_golden_pit_history` 并总结趋势

#### Scenario: 参数越界
- **WHEN** 传入的 `days` 超出 1-2000 范围
- **THEN** 工具按后端 Query 约束报错或后端返回校验错误，AI 提示用户调整参数

### Requirement: DCA 状态工具
Pi Agent 聊天模式 SHALL 提供 `get_golden_pit_dca_status` 工具，调用 `GET /api/v1/golden-pit/dca/status` 返回窗口活跃度、各 ETF 的执行进度（已投/待投天数、累计金额、剩余额度、趋势因子）。

#### Scenario: 查询 DCA 定投进度
- **WHEN** 用户询问"DCA 定投现在什么情况"
- **THEN** AI 调用 `get_golden_pit_dca_status` 并汇报窗口状态与各 ETF 执行进度

### Requirement: DCA 日志工具
Pi Agent 聊天模式 SHALL 提供 `get_golden_pit_dca_logs` 工具，参数为 `days`（默认 30）与可选 `fund_code`，调用 `GET /api/v1/golden-pit/dca/logs` 返回 DCA 执行历史（时间、指数、ETF、窗口天、金额、策略、状态）。

#### Scenario: 查询最近执行记录
- **WHEN** 用户询问"最近 DCA 买入了哪些 ETF"
- **THEN** AI 调用 `get_golden_pit_dca_logs` 并按返回记录汇总成交情况

### Requirement: ETF 配置工具
Pi Agent 聊天模式 SHALL 提供 `get_golden_pit_etf_configs` 工具，调用 `GET /api/v1/golden-pit/etf-configs` 返回所有黄金坑 ETF 定投配置（策略、日投金额、总上限、触发条件、启用状态）。

#### Scenario: 查询定投配置
- **WHEN** 用户询问"黄金坑 ETF 定投配置有哪些"
- **THEN** AI 调用 `get_golden_pit_etf_configs` 并列出策略与金额配置

### Requirement: ETF 配置更新工具（仅 trade 模式）
Pi Agent trade 模式 SHALL 提供 `update_golden_pit_etf_config` 工具，参数为 `fund_code` 与可选 `enabled`/`strategy`/`daily_amount`/`max_total_amount`，调用 `PUT /api/v1/golden-pit/etf-configs/{fund_code}` 更新定投配置；该工具 MUST NOT 注册进只读聊天模式工具集。

#### Scenario: 更新定投配置
- **WHEN** 用户在 trade 模式确认调整某 ETF 的日投金额
- **THEN** AI 调用 `update_golden_pit_etf_config` 并返回已更新字段

#### Scenario: 聊天模式不可见
- **WHEN** 聊天模式列出可用工具
- **THEN** `update_golden_pit_etf_config` 不在工具列表中

### Requirement: 前端工具注册与渲染
前端 `ChatContainer.tsx` SHALL 将 5 个只读黄金坑工具注册进 `chatTools` 数组，并同时加入 `COLLAPSIBLE_TOOLS` 与 `TOOL_LABELS`，保证工具结果可折叠渲染且显示中文名。

#### Scenario: 前端工具可用
- **WHEN** 前端聊天发起黄金坑相关查询
- **THEN** AI 可调用对应黄金坑工具，且工具结果以折叠卡片与中文标签渲染

#### Scenario: 前端仅只读工具
- **WHEN** 前端聊天 Agent 创建
- **THEN** 工具列表包含 5 个只读黄金坑工具，且不包含 `update_golden_pit_etf_config`（前端无独立 trade 模式，`tradingTools` 为 `chatTools` 别名）

### Requirement: 服务端工具注册
服务端 `tools.ts` SHALL 将 5 个只读黄金坑工具加入 `CHAT_TOOLS`，将 `update_golden_pit_etf_config` 加入 `TRADE_TOOLS`，且工具名、参数与前端定义保持一致。

#### Scenario: 服务端工具注入
- **WHEN** Pi Server 以 chat 模式创建 Agent
- **THEN** LLM 工具列表中包含 5 个只读黄金坑工具

#### Scenario: 双端定义一致
- **WHEN** 对比 `tools.ts` 与 `ChatContainer.tsx` 中的黄金坑工具定义
- **THEN** 每个工具的 `name` 与 `parameters` 完全一致

### Requirement: 系统提示词指引
`CHAT_SYSTEM_PROMPT`（`index.ts` 内嵌回退与后端 `db/prompt_seeds.py` 种子）SHALL 包含黄金坑工具使用时机说明，指导 AI 在用户询问黄金坑信号、DCA 定投进度、ETF 配置时调用对应工具。

#### Scenario: AI 识别黄金坑话题
- **WHEN** 用户消息涉及黄金坑、定投、贪婪值等关键词
- **THEN** AI 依据提示词指引优先调用对应黄金坑工具而非凭空作答
