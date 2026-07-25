## Context

后端 `position_add_monitor_log` 表和 API (`GET /api/v1/monitor/logs`, `GET /api/v1/monitor/logs/{id}`) 已就绪，前端只需新增页面对接。现有项目使用 React 18 + TypeScript 5 + Vite 5，样式采用 agent-theme.css 的 CSS 自定义属性系统，已有 PortfolioPage 的 cockpit 风格可参考。

## Goals / Non-Goals

**Goals:**
- 新增 `/monitor` 路由，TopNav 添加入口
- 列表页：条件筛选（标的、结果类型、日期范围）、分页、默认按时间倒序
- 行展开 / 侧栏查看单条日志的门控详情和技术指标 JSONB
- 复用 agent-theme.css 现有 token 体系，风格与 PortfolioPage 一致

**Non-Goals:**
- 不修改后端 API（已就绪）
- 不做实时推送 / WebSocket
- 不做日志导出功能

## Decisions

1. **筛选栏置于页面顶部，使用 inline filter bar**：与 cockpit 的紧凑风格一致，筛选条件少（3 个），不需要侧栏筛选面板
   - 备选：独立筛选侧栏 → 过度设计，4 个筛选字段不需要

2. **详情使用可展开行（expandable row）**：点击列表行在下方展开 JSONB 详情，优于弹窗 modal
   - 备选：modal → 打断浏览流，对比多条日志时需要反复开关
   - 备选：侧栏 drawer → 实现复杂度高，且列表变窄

3. **结果用彩色 badge 区分**：EXECUTED=绿、BLOCKED=红、HOLD=灰、SKIPPED=黄、OUTFLOW=蓝
   - 与止损监控页的 badge 体系一致

4. **分页使用简洁的 prev/next + 页码**：数据量不大（每 33s 一条 / 持仓），不需要复杂分页器

5. **日期筛选使用原生 `<input type="date">`**：简单够用，无需引入日期选择器库

## Risks / Trade-offs

- 日志表随时间增长可能变慢 → 已有 timestamp 索引 + 分页，短期无性能问题
- 展开行在移动端体验差 → 当前平台为桌面端优先，暂不考虑

## Open Questions

- 是否需要自动刷新日志列表？（建议先做手动刷新，保持与 PortfolioPage 的 `.cp-refresh-btn` 模式一致）
