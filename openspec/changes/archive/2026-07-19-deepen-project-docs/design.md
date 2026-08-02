## Context

当前项目规模约 100+ 源文件（16 API 路由、14 Service、12 Model、9 前端页面等），`openspec/project.md`仅 102 行，只描述目录骨架和模块名称。Claude 每次会话需读取 5-10 个文件才能定位目标代码，在 2 分钟读文件限制下效率低下。

## Goals / Non-Goals

**Goals:**
- 让 Claude 在首次读取 `project.md` 后即能精准定位任意模块的文件路径
- 让 Claude 理解请求→处理→持久化的完整数据流
- 让 Claude 知道如何新增 API/Service/前端页面的标准模板
- 文档自包含，不需要同时读多个文件

**Non-Goals:**
- 不修改任何源代码
- 不修改现有 `openspec/specs/` 下的行为规范
- 不创建超过 3 个文档文件（避免碎片化）

## Decisions

### 两文件结构
- `openspec/project.md`（~300 行）：一站式参考文档，含快速导航、数据流、模块速查、数据库 schema、前端组件树
- `openspec/docs/code-patterns.md`（~100 行）：可复制的代码模板

选择理由：一个主文档覆盖 90% 的场景；代码模式单独成文是因为它是"复制-修改"型内容，与导航型内容不同。

### project.md 五章节布局
1. **快速导航**：30+ 关键词 → 文件路径映射表，按业务领域分组
2. **核心数据流**：3 张 ASCII 图（交易请求流、行情数据流、后台监控流）
3. **模块速查**：每个 API/Service/Model 文件的关键类和核心方法
4. **数据库表结构**：PostgreSQL + SQLite 全部表
5. **前端组件树**：路由→页面→组件 + Zustand store

放弃的方案：按目录树逐文件描述（过于冗长，Claude 难以扫描）。

### 不拆分子文档
放弃"按子系统拆分 5-6 个文档"方案，因为 Claude 需要串行读取多个文件才能拼凑全貌，反而增加上下文消耗。

## Risks / Trade-offs

- [Risk] project.md 随项目增长可能过时 → 在 code-patterns.md 中注明"新增文件后更新 project.md 对应章节"
- [Risk] 300 行文档占用上下文空间 → 但相比每次读 10+ 源文件（可能 2000+ 行），净节省 85% 上下文
