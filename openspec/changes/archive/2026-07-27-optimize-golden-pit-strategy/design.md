## Context

黄金坑 v4 系统 (commit `2d2aca2`) 已实现趋势驱动仓位管理和三阶段窗口 (idle/waiting/buying)。但存在三个结构性问题：

1. **无退出规则**: 系统只定义何时买、买多少，没有定义何时卖。DCA 建仓完毕后头寸永远持有。
2. **全局参数一刀切**: `TURNING_CONSECUTIVE_DAYS=2`、`POSITION_TIERS` 的 50-75-100 对所有指数相同，无视回测揭示的巨大差异。
3. **独立决策浪费信息**: 每个指数独立判断，5 个指数同时入坑 vs 1 个入坑，仓位行为完全一样。

目标是在不改变核心架构的前提下，增加退出逻辑、参数差异化和共振系数三个模块。

## Goals / Non-Goals

**Goals:**
- 实现基于贪婪值回升的动态退出规则（P50→半仓，P70→清仓，拐点后回落→止盈）
- 为每个指数配置独立的入场阈值、仓位曲线倍率、拐点确认天数
- 多指数共振系数影响下单金额
- 新增完整入场→退出回测脚本 v7，用于校准参数

**Non-Goals:**
- 不改变趋势检测算法 (`_detect_trend`)
- 不修改数据库 schema（退出信号不持久化，由 DCA service 实时决策）
- 不改变前端页面布局（仅新增退出信号展示）
- 不引入新的外部数据源

## Decisions

### D1: 退出规则放在 GoldenPitService，不在 DCA Service

**选择**: 退出信号检测 (`_detect_exit_signal`) 放在 `GoldenPitService`，与 `_detect_trend` 并列。

**理由**: 退出信号依赖贪婪值 series 数据（percentile 计算），这些数据已在 GoldenPitService 中。DCA Service 调 `get_status()` 获取信号后决定是否卖出。分离关注点：GoldenPitService = 信号生产，DCA Service = 信号消费。

**替代方案**: 放在 DCA Service → 需要重复拉取 series 数据，浪费 API 调用。

### D2: 指数参数存在 CHINA_INDICES dict，不用 DB 表

**选择**: 扩展 `CHINA_INDICES` 字典，每个指数增加 `entry_percentile`、`exit_percentile`、`turning_days`、`position_multipliers` 等字段。

**理由**: 参数数量少（7 个指数 × 6 个新字段），不需要 CRUD 前端。修改参数 = 改代码提交，天然有版本控制。DB 表适合需要前端管理的 ETF 配置（金额、启停），算法参数不适合。

**替代方案**: 新建 `golden_pit_index_params` 表 → 过度工程化。

### D3: 共振系数为简单乘法因子

**选择**: 
```python
def _resonance_multiplier(indices) -> float:
    pit_count = sum(1 for i in indices if i["status"] == "golden_pit")
    if pit_count >= 4: return 1.3
    elif pit_count >= 3: return 1.2
    elif pit_count >= 2: return 1.0
    else: return 0.6
```

**理由**: 直接了当，不需要复杂模型。回测可以验证调优。仅对入坑（P5）指数计数，预警（P10）不算。

### D4: 退出执行通过现有交易 API

**选择**: DCA Service 新增 `_place_sell_order()` 方法，与 `_place_buy_order()` 对称，通过 `/api/v1/trades` 下限价卖单（限价 × 0.98）。

**理由**: 复用现有基础设施。卖出逻辑：每次 DCA 执行时检查已持仓的 ETF 是否触发退出信号 → 触发则卖出对应仓位。

### D5: 回测 v7 结构

**选择**: 新建 `scripts/backtest_golden_pit_v7.py`，模拟完整的"P10 入场 → 趋势跟踪 → P50/P70 退出"循环。与 v6 的关键区别：
- v6: 固定持有 N 天后卖出
- v7: 动态退出——贪婪值回升到 P50 以上卖一半，P70 以上清仓

输出：每笔交易的入场/退出日期、收益率、最大回撤，按指数和参数组合汇总。

## Risks / Trade-offs

- **[信号滞后]**: P50 退出意味着贪婪值已从底部回升一段，可能错过反弹前段 → 用 P30 卖一半 + P50 全清的两段退出缓解
- **[参数过拟合]**: 分指数参数可能过度拟合历史数据 → 初始参数从回测统计得出（如 20 日收益均值），留 20% 数据做验证
- **[卖出执行失败]**: 跌停或流动性不足可能导致卖出单无法成交 → 降为市价单重试，失败则第二天继续尝试
- **[共振系数放大风险]**: 多指数共振时 1.3x 仓位可能超过单指数上限 → 上限 = min(max_total * resonance, max_total)，不突破绝对上限

## Open Questions
- P30/P50 退出阈值是否需要每个指数独立？（初步用统一值，回测后决定）
- 是否需要"硬止损"（贪婪值跌破历史最低 1% 无条件卖出）？（v7 回测时对比两种方案）
