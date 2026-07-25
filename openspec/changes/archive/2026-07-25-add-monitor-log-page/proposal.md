## Why

加仓监控日志已落库到 PostgreSQL，并提供 `/api/v1/monitor/logs` API，但前端没有对应的展示页面。用户无法直观查看每次加仓检测的结果和原因，需要独立页面来查询、筛选和追踪历史检测记录。

## What Changes

- 新增前端页面 `MonitorLogPage`，路由 `/monitor`
- TopNav 导航栏新增"监控日志"入口
- 日志列表支持按标的、结果类型、日期范围筛选，分页展示
- 点击单条日志可展开查看门控详情（gate_details）和技术指标（trend_details）JSONB
- 仅前端新增，后端 API 已就绪

## Capabilities

### New Capabilities
- `monitor-log-page`: 加仓监控日志前端页面，包含列表查询、条件筛选、分页、详情展开

### Modified Capabilities
<!-- None — backend API already exists -->

## Impact

- 前端：新增 `MonitorLogPage.tsx`、`monitor-log-page.css`、路由注册、TopNav 入口
- 后端：无需修改（已有 `GET /api/v1/monitor/logs` 和 `GET /api/v1/monitor/logs/{id}`）
