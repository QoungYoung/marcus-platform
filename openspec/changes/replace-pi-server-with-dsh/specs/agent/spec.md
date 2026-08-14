## MODIFIED Requirements

### Requirement: Expert Panel (Reflect Mode)
The system SHALL support multi-role expert panel discussions orchestrated by AgentTeams within the DSH container.

#### Scenario: Start panel discussion
- **WHEN** POST /panel/reflect/stream with topic and roles is called
- **THEN** the DSH container orchestrates the multi-agent discussion with defined expert roles

#### Scenario: Panel roles
- **WHEN** panel is configured
- **THEN** available roles include: risk controller, trend trader, data analyst, devil's advocate, moderator

## ADDED Requirements

### Requirement: 服务端聊天通道经 DSH 桥接
The system SHALL route programmatic chat consumers (e.g. QQ Bot) through the DSH container's chat bridge instead of Pi Server.

#### Scenario: QQ Bot 消息处理
- **WHEN** QQ Bot 收到用户消息
- **THEN** 消息被转发到 DSH 容器聊天端点，返回的回复被发回 QQ

#### Scenario: Pi Server 不再存在
- **WHEN** 检查服务端聊天路由
- **THEN** 不再有任何请求指向 Pi Server（servers/pi-server 已移除）
