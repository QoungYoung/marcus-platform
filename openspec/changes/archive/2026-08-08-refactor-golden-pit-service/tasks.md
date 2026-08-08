## 1. 行为修复（阶段一）

- [x] 1.1 修复 `_build_index_info` 中 `prev_greed` 重复键：删除 dict 字面量里第 1347 行的 `"prev_greed": None`，保留基于 `sorted_series[-2]` 的计算值，并断言状态响应中 `prev_greed` 不再为 null（历史 ≥2 天时）
- [x] 1.2 修复 `_load_previous_percentile`：改为查询 `date < today` 的最近快照日期（`ORDER BY date DESC`），按该日期取全量快照；无早于今天的快照时返回 `{}`
- [x] 1.3 统一 ETA 基准：`_build_index_info` 的 `days_to_pit` 计算分支化——`use_fixed_greed=True` 用 `cfg["pit_greed"]` 计算 gap，否则用滚动窗口 P(pit_pct) 值
- [x] 1.4 修正 `get_score` 口径：`min_pct` 只统计 `core`/`satellite`/`defense` 指数，无可交易指数时回退 50.0
- [x] 1.5 冒烟验证阶段一：启动后端，手动触发 `golden_pit_morning` 任务与 `/api/v1/golden-pit/status`，核对 `prev_greed`、ETA、评分输出

## 2. 死代码清理与效率（阶段二）

- [x] 2.1 删除死代码：`_extract_arkvol_indices`（`golden_pit_service.py:1184`）、`GREED_ABSOLUTE_WARNING`、只写不读的 `is_first_p10_cross` 字段及其赋值
- [x] 2.2 提供 `get_golden_pit_service()` 模块级单例；`backend/app/api/golden_pit.py` 改用该单例，`golden_pit_dca_service.py` 与 `scheduler_service.py` 的任务入口改调单例
- [x] 2.3 批量 SQL：`get_history` 与 `_get_status_from_db` 的序列重建改为一次 `fund_code.in_(codes)` 查询后内存分组
- [x] 2.4 批量 SQL：`sync_full_series_to_db` 的 existing 日期查询改为一次 IN 查询覆盖全部指数

## 3. 结构拆分（阶段三）

- [x] 3.1 新建 `golden_pit_config.py`：迁移 `CHINA_INDICES`、`POSITION_TIERS`、阈值常量、`STRATEGY_LABELS`、`_display_config`、`_describe_entry_strategy`/`_describe_exit_strategy`/`_trend_label`/`_strategy_label`
- [x] 3.2 新建 `golden_pit_indicators.py`：迁移纯计算函数（percentile/trend/status/exit/p10 entry/resonance/`get_trend_factor`/交易日数学），原 `@staticmethod` 改为模块函数
- [x] 3.3 新建 `golden_pit_repository.py`：迁移全部 `GoldenPitSnapshot` DB 访问（history/snapshots/sync/save/reconstruct/prev_percentile）
- [x] 3.4 新建 `golden_pit_report.py`：迁移 `format_morning_report`/`_build_v2_summary`/`check_threshold_crossings`
- [x] 3.5 `golden_pit_service.py` 精简为编排类并 re-export 全部旧符号（`GoldenPitService`、`CHINA_INDICES`、`get_trend_factor`、`_strategy_label`、`_display_config`、`ArkvolServiceError` 等）
- [x] 3.6 冒烟验证 import 兼容：`python -c "from app.services.golden_pit_service import GoldenPitService, CHINA_INDICES, get_trend_factor, _strategy_label, _display_config, ArkvolServiceError"`，并运行一次 `golden_pit_morning` 与 `golden_pit_dca_morning` 任务

## 4. 可测试性与收尾

- [x] 4.1 为 `_build_index_info`、`_detect_golden_pit_window`、`_load_previous_percentile`、`save_daily_snapshot` 注入可选 `now`/`as_of` 参数（默认 `datetime.now()`）
- [x] 4.2 为 `golden_pit_indicators.py` 纯函数补充首批单测（percentile/trend/status/exit），覆盖 1.1-1.4 的修复场景
- [x] 4.3 更新 `openspec/specs/` 主规格：归档本变更后将 delta 同步到 `golden-pit-safety-brake`、`golden-pit-per-index-params`、`golden-pit-threshold-alerts`
