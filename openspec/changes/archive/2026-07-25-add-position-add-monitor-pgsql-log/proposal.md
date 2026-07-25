## Why

加仓监控 (PositionTierMonitor) 目前只通过 stderr、内存队列和 JSONL 文件输出日志，无法在页面上筛选、搜索和回溯加仓检测结果。无法知道 "今天哪些票被趋势强度拦截了" "某个标的最近一周的加仓检测历史" 等信息。将日志落地到 PostgreSQL 后，可在前端页面展示监控日志，支持按标的、日期、结果类型等维度筛选。

## What Changes

- 新增 PostgreSQL 表 `position_add_monitor_log`，记录每次加仓条件检测的完整结果
- 在 `PositionTierMonitor._check_all_positions()` 中，每次检查完每只持仓后写入一行日志
- 平表列存储关键字段（标的、结果类型、盈亏比等）供列表筛选排序；JSONB 列存储门控详情和趋势指标详情供详情展示
- 新增 API 端点查询加仓监控日志（列表 + 详情）
- 在 `database.py` 的 `init_db()` 中注册新模型

## Capabilities

### New Capabilities
- `position-add-monitor-log`: 加仓监控日志持久化到 PostgreSQL，包含每次检测的层级评估结果、门控仲裁详情、趋势强度指标

### Modified Capabilities
<!-- 无已有 spec 需要修改 -->

## Impact

- `backend/app/models/` — 新增 `position_add_log.py` 模型文件
- `backend/app/services/position_tier_monitor.py` — 在 `_check_all_positions()` 中添加数据库写入调用
- `backend/app/database.py` — `init_db()` 中注册新模型
- `backend/app/api/` — 新增 API 端点（列表查询 + 详情）
