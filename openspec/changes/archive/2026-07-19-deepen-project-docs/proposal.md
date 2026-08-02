## Why

当前 `openspec/project.md` 仅覆盖项目架构骨架（102行），Claude 每次对话仍需大量读取源文件才能定位代码和理解数据流。需要一份深度文档，让 Claude 在会话开始时读一次就能高效导航整个项目。

## What Changes

- 重写 `openspec/project.md`，从 102 行扩展到 ~300 行，新增 5 个章节：
  - **快速导航表**：关键词 → 精准文件路径映射，修 bug 时秒级定位
  - **核心数据流**：交易请求流、行情数据流、后台监控流的 ASCII 图
  - **模块速查**：每个 API/Service/Model 文件的关键类和核心方法
  - **数据库表结构**：PostgreSQL + SQLite 全部表名→模型→字段
  - **前端组件树**：路由→页面→组件关系 + Zustand store 结构
- 新增 `openspec/docs/code-patterns.md`：标准代码模板（新增 API 端点、新增 Service、新增前端页面）

## Capabilities

### New Capabilities
- `project-documentation`: 深度项目文档，包含快速导航、数据流图、模块速查、数据库 schema、前端组件树

### Modified Capabilities
<!-- No existing specs change behavior — this is purely documentation -->

## Impact

- 修改：`openspec/project.md`（重写）
- 新增：`openspec/docs/code-patterns.md`
- 代码行为无变化，无 API/Dependency/Breaking 影响
