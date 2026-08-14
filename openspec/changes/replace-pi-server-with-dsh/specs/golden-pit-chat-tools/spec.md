## MODIFIED Requirements

### Requirement: 服务端工具注册
DSH 容器 SHALL 将 5 个只读黄金坑工具注册为原生 tool，将 `update_golden_pit_etf_config` 注册进 trade 模式工具集，且工具名、参数与前端定义保持一致。

#### Scenario: 服务端工具注入
- **WHEN** DSH 以 chat 模式创建 Agent
- **THEN** LLM 工具列表中包含 5 个只读黄金坑工具

#### Scenario: 双端定义一致
- **WHEN** 对比 DSH 工具定义与 `ChatContainer.tsx` 中的黄金坑工具定义
- **THEN** 每个工具的 `name` 与 `parameters` 完全一致

### Requirement: 系统提示词指引
系统 Prompt（DSH 会话 Prompt 与后端 `db/prompt_seeds.py` 种子）SHALL 包含黄金坑工具使用时机说明，指导 AI 在用户询问黄金坑信号、DCA 定投进度、ETF 配置时调用对应工具。

#### Scenario: AI 识别黄金坑话题
- **WHEN** 用户消息涉及黄金坑、定投、贪婪值等关键词
- **THEN** AI 依据提示词指引优先调用对应黄金坑工具而非凭空作答
