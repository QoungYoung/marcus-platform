## Context

`backend/app/services/golden_pit_service.py` 共 2178 行，单一文件混合五种职责：配置常量（~400 行）、模块级工具函数、`GoldenPitService` 编排、DB 持久化、报告/预警文本。已确认四处行为缺陷（`prev_greed` 重复键、跨周末穿越预警丢失、ETA 基准不一致、v1 评分混入不可交易指数）与两类效率问题（跨调用无缓存共享、per-index 循环 SQL）。

约束：API 模块（`backend/app/api/golden_pit.py`）import `GoldenPitService`/`_strategy_label`/`_display_config`/`ArkvolServiceError`；DCA 服务（`golden_pit_dca_service.py`）import `GoldenPitService`/`CHINA_INDICES`/`get_trend_factor`；调度器（`scheduler_service.py`）import `GoldenPitService`。拆分时必须保持这些 import 可用。系统为单进程部署（APScheduler 与 FastAPI 同进程）。

## Goals / Non-Goals

**Goals:**
- 修复 4 处行为缺陷，恢复与既有 spec 一致的行为（飞刀保护、穿越预警、ETA、评分口径）。
- 删除死代码（`_extract_arkvol_indices`、`GREED_ABSOLUTE_WARNING`、只写不读的 `is_first_p10_cross`）。
- 进程内共享 `GoldenPitService` 单例与 TTL 缓存，消除 ArkVol 请求跨任务重复拉取。
- 将 per-index 循环 SQL 批量化为单次 `fund_code.in_()` 查询。
- 按职责拆分为 config/indicators/repository/report 四个模块，`golden_pit_service.py` 保留编排类并 re-export 兼容。
- 注入时钟（`now`/`as_of` 参数）提升可测试性。

**Non-Goals:**
- 不改变对外 API 响应 schema（`/api/v1/golden-pit/*`）与前端字段约定（仅修正字段语义：`prev_greed` 从恒 null 变为真实值）。
- 不新增第三方依赖，不改数据库 schema。
- 不重构 `golden_pit_dca_service.py` 内部逻辑（仅消费修复后的 `prev_greed`）。
- 不启用当前不可达的 pi_server 价格代理路径（`_extract_pi_server_indices` 保留但标记为扩展点，不修其窗口失真问题）。
- 不引入 dataclass/Pydantic 化所有内部 dict（收益/风险比低，留作后续）。

## Decisions

1. **模块拆分按职责、不按类**
   新建四个模块，`golden_pit_service.py` 作为兼容入口 re-export：
   - `golden_pit_config.py`：`CHINA_INDICES`/`POSITION_TIERS`/阈值常量/`STRATEGY_LABELS`/`_display_config`/策略描述函数。
   - `golden_pit_indicators.py`：纯函数——percentile、trend、status、exit、p10 entry、resonance、`get_trend_factor`、交易日数学。现 13 个 `@staticmethod` 抽为模块函数。
   - `golden_pit_repository.py`：全部 `GoldenPitSnapshot` DB 访问（history/snapshots/sync/save/reconstruct/prev_percentile）。
   - `golden_pit_report.py`：`format_morning_report`/`_build_v2_summary`/`check_threshold_crossings`。
   - `golden_pit_service.py`：`GoldenPitService` 编排 + ArkVol 拉取 + 管道 + 全部旧符号 re-export。
   备选：单文件仅删死代码 → 治标不治本，文件仍 1700+ 行；一次性 dataclass 化 → 改动面过大。

2. **单例 + TTL 缓存复用**
   提供 `get_golden_pit_service()` 模块级单例（沿用 API 模块 `_get_service()` 模式），DCA 与调度器改调单例。`_cache` 保留 TTL 语义；`save_daily_snapshot` 依旧显式清缓存保证盘前取新数据。线程安全：单例内仅 dict 读写，GIL 下安全。
   备选：Redis 进程外缓存 → 过度设计；每任务新建实例 → 维持现状（放弃）。

3. **`prev_greed` 修复 = 删除重复键**
   `_build_index_info` 字典字面量中删除第 1347 行的 `"prev_greed": None`，保留第 1312 行基于 `sorted_series[-2]` 的计算值。行为变化：DCA 飞刀保护恢复触发（与 `golden-pit-safety-brake` spec 一致）。

4. **穿越预警基准 = 最近快照日期**
   `_load_previous_percentile` 改为一条查询：取 `date < today` 的最大 `date`（`ORDER BY date DESC LIMIT 1`），再按该日期取全部快照；无任何早于今天的快照则返回 `{}`（跳过检测）。替换现有的昨天/前天两个自然日查询。

5. **ETA 基准与状态判定对齐**
   `_build_index_info` 的 `days_to_pit` 计算分支化：`use_fixed_greed=True` → gap 到 `cfg["pit_greed"]`；否则 → gap 到滚动窗口 P(pit_pct) 值。`decline_rate` 保持现有口径。

6. **v1 评分口径收敛**
   `get_score` 的 `min_pct` 计算前先过滤 `tier in ("core","satellite","defense")`；若无可交易指数，回退 `min_pct=50.0`。

7. **批量 SQL**
   `get_history`、`_reconstruct_series_from_db`（改为一次 `fund_code.in_(codes)` 后内存分组）、`sync_full_series_to_db` 的 existing 日期查询（一次 IN 查询所有指数）统一批量化。索引 `(date, fund_code)` 唯一约束已覆盖。

8. **注入时钟**
   所有 `datetime.now()` 相关入口（`_build_index_info` 的 `today_str`、`_detect_golden_pit_window`、`_load_previous_percentile`、`save_daily_snapshot` 的 `today`）改为可选 `now: Optional[datetime] = None` 参数，默认 `datetime.now()`，便于离线回放与单测。

## Risks / Trade-offs

- [模块拆分导致 import 回归] → 保留 `golden_pit_service.py` 全量 re-export；拆分后冒烟执行 `python -c "from app.services.golden_pit_service import GoldenPitService, CHINA_INDICES, get_trend_factor, _strategy_label, _display_config, ArkvolServiceError"` 并触发一次调度任务 dry-run。
- [单例缓存使跨任务共享状态] → 快照/同步任务显式清缓存（现状已如此）；单进程部署下无一致性问题。
- [ETA 基准统一改变盘前报告文案] → 仅影响展示文本，不影响交易判定与订单金额；可用历史快照对比新旧预测。
- [飞刀保护恢复后买入次数可能减少] → 与 spec 要求一致，属预期行为修复，DCA 日志 `status=safety_brake` 可审计。
- [批量 SQL 一次取 13 指数数据量增大] → 单表行数有限（每指数每日 1 行），内存可忽略。

## Migration Plan

1. 阶段一（行为修复，独立可回滚）：`prev_greed` 重复键、穿越基准、ETA 基准、get_score 口径；冒烟验证报告与预警。
2. 阶段二（清理与效率）：删死代码、单例复用、批量 SQL。
3. 阶段三（结构拆分）：抽 config/indicators/repository/report，保持 re-export 与行为等价。
4. 回滚策略：每个阶段一个 commit；拆分阶段若发现问题可直接回退到阶段二 commit（行为已等价，不丢修复）。
5. 验证：后端启动冒烟 + 手动触发 `golden_pit_morning` 任务 + 对比修复前后 `/api/v1/golden-pit/status` 响应字段。

## Open Questions

- 是否在本次补建 `backend/tests/` 并为 `golden_pit_indicators.py` 纯函数写首批单测？建议做（拆出纯函数后成本低），但若用户希望保持纯重构可省略。
- `_extract_pi_server_indices` 的价格代理窗口失真与 `_trading_days_between` 的交易日近似，是否纳入本变更？默认不纳入，列后续优化项。
