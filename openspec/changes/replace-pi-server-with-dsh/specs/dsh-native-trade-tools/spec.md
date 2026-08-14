## Purpose

将交易写工具（下单、撤单、仓位计算、黄金坑 ETF 配置更新等）注册为 DSH 原生 tool——结构化参数校验、强制 JSON Schema，供 chat/trade/panel 各模式共用；只读查询工具保持 skill 形态，二者边界明确。

## ADDED Requirements

### Requirement: 写工具原生注册
交易写工具 SHALL 以 DSH 原生 tool 形式注册（结构化参数定义与校验），至少包括：`place_order`（下单）、`cancel_order`（撤单）、`calc_position`（仓位计算）、`update_golden_pit_etf_config`（黄金坑 ETF 定投配置更新）。

#### Scenario: 工具可调用
- **WHEN** chat/trade 模式的 Agent 调用写工具
- **THEN** 工具按 JSON Schema 校验参数并执行对应 Backend API 调用，返回结构化结果

#### Scenario: 参数校验失败
- **WHEN** 写工具收到缺失或非法参数
- **THEN** 工具返回参数错误，不执行任何写操作

### Requirement: 写工具模式边界
写工具 MUST NOT 注册进只读场景的工具集：聊天模式（chat）SHALL 只暴露只读工具，`place_order` / `cancel_order` SHALL 仅出现在 trade 模式；`update_golden_pit_etf_config` SHALL 仅出现在 trade 模式。

#### Scenario: 聊天模式无写工具
- **WHEN** chat 模式列出可用工具
- **THEN** 写工具不在列表中

#### Scenario: trade 模式含写工具
- **WHEN** trade 模式列出可用工具
- **THEN** 写工具在列表中

### Requirement: 只读工具保持 skill 形态
只读查询工具（行情、技术指标、资金流、黄金坑状态/DCA 等）SHALL 保持 skill 形态（`marcus-panel-tools`），以 HTTP API 调用约定描述，供专家组成员与聊天 Agent 使用。

#### Scenario: 只读工具可用
- **WHEN** 专家组成员或聊天 Agent 需要只读数据
- **THEN** 通过 `marcus-panel-tools` skill 描述的 HTTP API 获取数据

#### Scenario: 只读工具无写权限
- **WHEN** 检查只读工具集
- **THEN** 不包含 `place_order` / `cancel_order` 等写操作

### Requirement: 工具命名与契约一致性
DSH 原生写工具的 `name` 与 `parameters` SHALL 与既有 `tools.ts` 定义保持一致（参数名、默认值、端点映射不变），保证 Prompt 与调用方无需感知迁移。

#### Scenario: 契约兼容
- **WHEN** 对比 DSH 写工具定义与迁移前 `tools.ts` 定义
- **THEN** 每个写工具的 `name`、`parameters` 与调用的 Backend 端点完全一致
