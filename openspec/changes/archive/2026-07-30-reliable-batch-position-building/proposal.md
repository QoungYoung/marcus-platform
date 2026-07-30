## Why

当前黄金坑 DCA 分批建仓机制存在回测最优策略与生产执行之间的根本性断裂：回测为每个指数优化了 `dca_strategy`（如中证1000用 uniform_3、科创50用 lump_entry），但 `execute_golden_pit_dca()` 完全忽略该字段，转而使用一刀切的"趋势驱动仓位分级"（pre_turn=3% / turning=50% / accelerate=75% / full=100%）。这导致分批建仓策略名存实亡，回测优化结论无法落地。

## What Changes

- **DCA 基准权重层**：`execute_golden_pit_dca()` 接入 `_strategy_weights()`，将回测优化的 `dca_strategy` 作为建仓节奏的基准
- **趋势调节因子层**：保留并改良趋势驱动逻辑，从硬编码的四级跳跃（3%→50%→75%→100%）改为平滑调节因子（0.1x→0.5x→1.0x→1.2x→1.5x），与 DCA 权重相乘
- **安全制动层**：新增三种硬约束——假信号暂停（greed 突破 entry_greed）、飞刀保护（单日跌幅>2%跳过）、累计硬截断
- **二次信号机制**：当 DCA 窗口内贪婪继续创新低时，以新低点为锚重置窗口
- **CHINA_INDICES 参数扩展**：新增 `trend_factors` 分指数调节因子覆盖、`dca_fallback` 窗口超时兜底策略

## Capabilities

### New Capabilities
- `golden-pit-dca-schedule`: DCA 基准权重调度 — 信号触发后按 `dca_strategy` 定义的固定时间表生成每日买入权重，`uniform_3` 三天等权、`lump_entry` 首日全仓等，作为建仓节奏的速度上限
- `golden-pit-trend-modulation`: 趋势状态调节因子 — 将当前趋势状态（declining/bottoming/turning/accelerating/full）映射为平滑的仓位乘数（0.1x~1.5x），与 DCA 基准权重相乘后得出实际买入金额
- `golden-pit-safety-brake`: 安全制动系统 — 假信号检测（突破 entry_greed→暂停）、飞刀保护（单日跌幅>2%→跳过）、累计硬截断三项硬约束

### Modified Capabilities
- `golden-pit-per-index-params`: 新增 `trend_factors`（分指数趋势调节因子表）和 `dca_fallback`（DCA 窗口超时后兜底策略）两个配置字段
- `golden-pit-exit`: 退出信号中的 `fallback_exit` 触发条件需兼容新的分批建仓节奏——当 DCA 窗口超时而非持仓超时时触发

## Impact

- `backend/app/services/golden_pit_dca_service.py`: 核心修改，`execute_golden_pit_dca()` 重构仓位计算逻辑，从四级跳跃改为 DCA权重 × 趋势因子
- `backend/app/services/golden_pit_service.py`: CHINA_INDICES 新增 `trend_factors` 和 `dca_fallback` 字段
- `backend/app/models/golden_pit_dca_log.py`: 可能需要新增 `schedule_day` 字段追踪当前处于 DCA 窗口第几天
- 前端 `GoldenPitPage.tsx`: 展示新的分批建仓进度（当前窗口第几天、剩余计划、趋势状态）
- **非 BREAKING**：API 返回结构和数据库 schema 为增量添加，现有字段不变
