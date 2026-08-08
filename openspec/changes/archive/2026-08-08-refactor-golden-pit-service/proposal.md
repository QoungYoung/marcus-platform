## Why

`backend/app/services/golden_pit_service.py` 已膨胀到 2178 行，混合了配置、纯计算、DB 持久化、报告/预警和编排五种职责，且存在多处已确认的行为缺陷：`prev_greed` 重复键导致 DCA 飞刀保护永不触发、跨周末/节假日阈值穿越预警静默丢失、ETA 预测与固定阈值判定基准不一致。趁功能稳定期做一次"行为修复 + 死代码清理 + 模块拆分 + 缓存/查询优化"，降低后续迭代和加指数时的维护成本。

## What Changes

- **修复 `prev_greed` 重复键**：`_build_index_info` 中字典重复定义 `prev_greed`（1312/1347 行），后值 `None` 覆盖先算出的前一交易日贪婪值，导致 `golden_pit_dca_service.py` 的飞刀保护（单日跌幅 >2pp 跳过买入）永不触发。删除重复键，恢复该安全制动。
- **修复跨交易日穿越预警丢失**：`_load_previous_percentile` 只查询昨天/前天两个自然日，周一和节后首日无快照 → `check_threshold_crossings` 空跑。改为查询"今天之前最近的快照日期"。
- **统一 ETA 预测基准**：`use_fixed_greed` 指数状态按 `pit_greed` 判定，但 `days_to_pit` 用滚动 P5 值计算 gap，两者基准不一致。改为按与状态判定相同的固定阈值（或相同的百分位窗口口径）预测入坑日。
- **修正 v1 综合评分口径**：`get_score` 的 `min_pct` 当前混入 `drop`/`watch` 指数，放弃级指数低分位会拉高评分。改为只统计 core/satellite/defense 可交易指数。
- **删除死代码**：`_extract_arkvol_indices`（已由 ai-summary 路径取代）、`GREED_ABSOLUTE_WARNING`、只写不读的 `is_first_p10_cross` 字段、不可达的 `entry_offset` 文案逻辑。
- **共享服务实例与 TTL 缓存**：DCA 服务与调度器每次任务新建 `GoldenPitService()`，实例级 `_cache` 失效，ArkVol 请求每日重复拉取。改为进程级单例复用（与 API 模块 `_get_service()` 一致）。
- **批量化 DB 查询**：`get_history`/`_reconstruct_series_from_db`/`sync_full_series_to_db` 的 per-index 循环查询（13 条 SQL）改为一次 `fund_code.in_()` 查询后内存分组。
- **按职责拆分模块**：拆出 config / 纯指标计算 / DB 仓储 / 报告四个模块，`golden_pit_service.py` 保留 `GoldenPitService` 编排并 re-export 兼容现有 import（API、DCA、调度器无需改动）。
- **注入时钟**：`datetime.now()` 改为可注入的 `now`/`as_of` 参数，提升可测试性。

## Capabilities

### New Capabilities
- `golden-pit-threshold-alerts`: 黄金坑阈值穿越预警（P10 预警 / P5 入坑），要求对比基准为"最近一个交易日的快照"，保证周一与节后首日不丢预警。

### Modified Capabilities
- `golden-pit-safety-brake`: 状态数据 SHALL 提供前一交易日贪婪值 `prev_greed`（修复重复键后），飞刀保护 SHALL 据此触发。
- `golden-pit-per-index-params`: 入坑 ETA 预测 SHALL 与状态判定使用一致的固定阈值基准；v1 综合评分 SHALL 只统计可交易指数（core/satellite/defense）。

## Impact

- `backend/app/services/golden_pit_service.py`: 拆分、删死代码、修复 4 处行为缺陷（本变更主要改动点）。
- `backend/app/services/golden_pit_dca_service.py`: 飞刀保护恢复生效（消费 `prev_greed`），建议复用共享单例。
- `backend/app/services/scheduler_service.py`: golden_pit 任务改用共享服务实例。
- `backend/app/api/golden_pit.py`: 无接口变更（`_strategy_label`/`_display_config` 通过 re-export 保持可用）。
- 新增模块文件（config/indicators/repository/report），不新增第三方依赖；前端无 schema 变化。
