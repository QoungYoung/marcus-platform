## Context

`PositionTierMonitor` 是 4 个后台监控器之一，每 33 秒轮询一次，对每只持仓执行 3 层加仓评估：层级评估 → 门控仲裁 → 自动执行。目前日志通过 stderr print、内存队列（max 500 条）、JSONL 文件输出，无法在页面上查询。

本设计将监控日志持久化到已有 PostgreSQL 实例（`marcus_trading`），沿用项目已有的 SQLAlchemy 同步模式，参考 `stop_loss_log` 表的写法。

## Goals / Non-Goals

**Goals:**
- 每次加仓检测为每只持仓写入一行 PostgreSQL 日志
- 提供 API 端点供前端列表查询（筛选 + 分页 + 排序）
- 提供 API 端点获取单条日志的门控详情（JSONB 展开）
- 日志模型与已有 `stop_loss_log` 风格一致

**Non-Goals:**
- 不改造现有 JSONL 文件和 QQ 推送（保留并行运行）
- 不在本次落地前端页面组件（只在后端提供 API）
- 不存储历史日志的自动清理（后续可加定时任务）

## Decisions

### Decision 1: 平表 + JSONB 混合模型

平表列存放列表页展示/筛选所需的字段（symbol、result、price、pnl），JSONB 存放门控详情和趋势指标。

**替代方案考虑**：
- 纯平表：门控每增加一项需要 ALTER TABLE，维护成本高
- 纯 JSONB：筛选需走 JSONB 查询，慢且不能用普通索引

**选择理由**：兼顾列表查询性能和结构灵活性。

### Decision 2: 每次检测写一行

在 `_check_all_positions()` 循环中，每处理完一只持仓就写一行。不等到整轮结束再批量写入。

**替代方案考虑**：
- 批量写入：需要攒数据，中途 crash 丢日志；且代码改动更大
- 异步写入：引入 asyncpg 依赖，与项目同步风格冲突

**选择理由**：简单、即时、容灾好（每行写一次不怕丢）。日写入量约 10×6×109 ≈ 6500 行/天，PostgreSQL 完全能承受。

### Decision 3: 直接使用 SessionLocal 会话

模型在 `_check_all_positions()` 中直接 `SessionLocal()` 获取会话写入，不走 FastAPI 依赖注入。

**替代方案考虑**：
- 依赖注入：_check_all_positions 是 daemon thread 方法，不经过 FastAPI 请求生命周期，无法使用 Depends

**选择理由**：和 `stop_loss_monitor.py` 写入 `stop_loss_log` 的模式一致。

### Decision 4: API 端点独立文件

监控日志的 API 放在新的文件 `backend/app/api/monitor_log.py` 中，挂载到 `/api/v1`，标签 `Monitor Log`。

**选择理由**：职责单一，不往已有 API 文件里塞新路由。参考 scheduler.py 中 stop-loss-monitor 相关端点的做法。

## Risks / Trade-offs

- **监控线程中写DB可能阻塞**：写入失败会打 error log 但不影响主循环继续。使用短超时连接
  → Mitigation: try/except 包裹写入，失败仅 log，不中断监控循环

- **JSONB 无法建普通 B-tree 索引**：对 JSONB 中高频筛选字段（如 gate.passed）
  → Mitigation: 关键判断结果已提为平表列（result, block_reason），JSONB 仅供详情展示

- **日志表无限增长**：日均 6500 行 ≈ 每月 20 万行
  → Mitigation: 暂时无风险（PostgreSQL 能处理百万级），后续可加定时清理（保留 90 天）
