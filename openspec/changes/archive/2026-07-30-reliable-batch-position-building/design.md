## Context

当前黄金坑 DCA 执行引擎（`golden_pit_dca_service.py:execute_golden_pit_dca`）使用纯趋势驱动的仓位分级模型：

```
pre_turn(3%) → turning(50%) → accelerate(75%) → full(100%)
```

这个模型完全忽略了回测优化出的 `dca_strategy` 字段。回测 (`backtest_golden_pit_ultimate.py`) 针对每个指数穷举了 9 种 DCA 策略，得出的结论是：
- 高胜率、强趋势指数（科创50、中证500）：`lump_entry` 最优
- 高波动、低胜率指数（中证1000、恒生指数）：`uniform_3` 最优

两者的关系应该是**乘积而非替代**：DCA 策略定义建仓节奏上限，趋势状态决定当前置信度折扣。

## Goals / Non-Goals

**Goals:**
- 让 `dca_strategy` 字段真正驱动建仓节奏，回测优化结论落地
- 保留趋势驱动的优势（拐点确认后加速建仓），但改为平滑因子而非硬编码跳跃
- 新增安全制动机制防止假信号和飞刀行情
- 对所有现有指数的 `dca_strategy` 配置向后兼容

**Non-Goals:**
- 不修改回测脚本本身
- 不修改 `_strategy_weights()` 的权重计算
- 不改变前端展示结构（仅在现有卡片中增加进度信息）
- 不引入新的外部依赖

## Decisions

### Decision 1: 三明治模型 — DCA基准 × 趋势因子

**选择**: `daily_amount = dca_weight[day] × trend_factor × max_total`

**替代方案**:
- A) 直接用 DCA 权重替代趋势驱动 → 放弃了趋势确认带来的安全边际
- B) 保留趋势驱动，DCA 策略仅作为"建议"展示 → 目前的状态，无改进
- C) DCA 权重和趋势因子取 max → 可能在拐点前过度买入

**理由**: 乘积模型让两个信号各司其职——DCA 控制节奏（多快可以买完），趋势控制置信度（现在该不该买这么多）。当趋势朝向有利方向时，趋势因子放大 DCA 权重；当趋势恶化时，趋势因子缩小 DCA 权重。

### Decision 2: 趋势因子为连续映射而非四级跳跃

**选择**: 5 级平滑因子映射

| 趋势状态 | 条件 | 因子 | 含义 |
|----------|------|------|------|
| declining | days_rising=0, greed下降 | 0.1x | 飞刀减速 |
| bottoming | days_rising=1 | 0.5x | 初步试探 |
| turning | days_rising=2 | 1.0x | 标准节奏 |
| accelerating | days_rising=3 | 1.2x | 加快速度 |
| full | days_rising≥4 | 1.5x | 快速满仓 |

**替代方案**: 保持 3%/50%/75%/100% 四级跳跃 → 跳跃过大且与 DCA 权重冲突

**理由**: 因子化的优势在于与 DCA 权重相乘后自然产生合理的金额梯度，且每个指数可以有自己的 `trend_factors` 覆盖表。

### Decision 3: 安全制动在金额计算之后独立执行

**选择**: 三层硬约束作为独立检查步骤，不嵌入因子计算

```
1. 假信号暂停: greed > entry_greed → skip
2. 飞刀保护: 单日跌幅 > 2% → skip  
3. 累计硬截断: total_invested ≥ max_total → skip
```

**理由**: 安全制动是"是/否"二元决策，不应该用因子连续缩放。独立执行使逻辑更清晰、更易审计。

### Decision 4: 二次信号机制用 "窗口重置" 实现

**选择**: 当 DCA 窗口内 greed 创新低（当前 greed < 信号触发日的 greed − 5%容差），重置 `schedule_day=0`，以新低点为锚重新开始 DCA 窗口

**替代方案**: 追加额外买入 → 可能突破 max_total 限制

**限制**: 最多重置一次，防止无限循环

### Decision 5: 分指数 trend_factors 覆盖表

**选择**: 每个指数可在 CHINA_INDICES 中定义自己的 `trend_factors`，未定义时使用全局默认值

```python
# 全局默认 (CHINA_INDICES 之外的常量)
DEFAULT_TREND_FACTORS = {
    "declining": 0.1, "bottoming": 0.5,
    "turning": 1.0, "accelerating": 1.2, "full": 1.5,
}

# 指数级覆盖示例 (中证1000)
"trend_factors": {
    "declining": 0.15,  # 波动大，拐点前稍多投一点
    "full": 1.3,         # 但趋势确立后也不宜过猛
}
```

**理由**: 回测显示不同指数的收益来源不同——中证1000 靠底部累积降低成本，科创50 靠趋势确立后快速上仓位——统一的趋势因子无法同时适配两者。

## Risks / Trade-offs

- **[风险] DCA 窗口期内趋势未确认，导致仓位累积不足**: 回测中 `turning_days=0` 意味着最优策略不需要等趋势确认。在生产中，如果 greed 持续底部震荡而不回升，趋势因子会压制仓位。→ **缓解**: `dca_fallback` 设置窗口最大天数，超时后趋势因子强制=1.0，按 DCA 基准完成剩余买入
- **[风险] 二次信号重置可能造成过度交易**: 在极端熊市中，greed 不断新低，如果不限制重置次数，可能反复重置。→ **缓解**: 最多重置 1 次，且重置后的总窗口天数不超过 30 天
- **[风险] 趋势因子可能放大回撤**: 当 trend=accelerating (1.2x) 或 full (1.5x) 时，因子放大 DCA 权重，可能导致在市场快速反弹中追高。→ **缓解**: `加速阈值`: 当 greed 已回到 entry_greed 以上时，趋势因子不再放大（上限=1.0）

## Migration Plan

1. **Phase 1**: 修改 `execute_golden_pit_dca()` 的仓位计算逻辑，接入 DCA 权重和趋势因子。保持 API 返回结构不变，仅金额计算逻辑变化。
2. **Phase 2**: 在数据库 DCA log 中新增 `schedule_day` 字段，追踪窗口内进度（向下兼容，NULL=旧数据）。
3. **Phase 3**: 前端展示 DCA 进度信息（当前窗口天数、剩余买入计划）。
4. **回滚**: 所有逻辑在 `golden_pit_dca_service.py` 内，如需回滚只需恢复旧版仓位计算分支，无需数据库迁移回滚。

## Open Questions

- 单日跌幅阈值（飞刀保护）的 2% 是否过松/过紧？建议上线后根据实际触发频率调参
- 二次信号重置的 5% 容差是否需要区分不同指数的波动率？（科创50 的 daily swing 远大于沪深300）
