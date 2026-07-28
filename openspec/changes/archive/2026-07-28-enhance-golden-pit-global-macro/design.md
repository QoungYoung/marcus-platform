## Context

黄金坑 DCA 策略目前在 `golden_pit_service.py` 的 `_compute_triple_confirmation()` 中已获取 `global-capital-flow` API 数据，但仅读取 `sentiment_score` 做布尔判断（< 30 = 确认）。API 返回的流动性闸门状态、全球风险偏好趋势序列、五市场横向对比等数据全部丢弃。

现在将这些数据接入黄金坑策略的四个决策环节：买入闸门、拐点验证、仓位系数、退出信号。

## Goals / Non-Goals

**Goals:**
- 流动性闸门收紧时硬停止买入，防止在系统性紧缩中接飞刀
- 全球风险偏好趋势与 A 股拐点交叉验证，过滤假拐点
- 全球宏观系数叠加到现有仓位计算，恐慌降仓、共振加仓
- 全球极端贪婪时提前止盈

**Non-Goals:**
- 不增加数据库迁移（纯内存计算 + API 数据）
- 不改变现有的 P10/P5 信号触发逻辑
- 不引入新的 API 调用（复用已有的 `_cached_fetch("global-capital-flow")`）
- 不做五市场轮动选股（那是另一个独立功能）

## Decisions

### 决策 1: 流动性闸门判断 —— 从 sentiment_score 推断

**选择**: 用 `sentiment_score` 和 `sentiment_label` 作为闸门代理，阈值设在 score < 20（极度恐慌）。

**替代方案与排除理由**:
- 从 `original_page_data` 解析具体的美元/美债指标 → API 字段名未文档化，强行解析脆弱
- 用外部 DXY/美债收益率 API → 增加依赖，且 ArkVol 已综合建模

**逻辑**: score < 20（极度恐慌区域）意味着全球资金全面避险，A 股不可能独善其身。此时暂停买入。

### 决策 2: 全球趋势计算 —— 从 series 序列推断

**选择**: 从 GCF 响应的 `series` 数组中找到 A 股/新兴市场对应的序列，用最近 5 天的 `sentiment_score`（或该序列自身的 score 字段）计算趋势方向。连续2天上升 = 全球风险偏好改善。

**逻辑**: 如果 `series` 不可用，回退到 `original_page_data` 中的市场级序列。如果都不可用，则此项功能静默跳过（不阻断主流程）。

### 决策 3: 仓位系数 —— 乘法叠加

**选择**: 在现有的 `resonance_multiplier` 之后乘一个 `global_macro_coefficient`，范围 [0, 1.5]。

| 全球状态 | 系数 |
|----------|:---:|
| 极度恐慌 (score <= 20) | 0 |
| 恐慌 (20 < score <= 35) | 0.5 |
| 中性 (35 < score <= 55) | 1.0 |
| 贪婪 (55 < score <= 75) | 1.0 |
| 极度贪婪 (score > 75) | 0.8 |

系数不改变头寸上限（仍受 `max_total_amount` 约束）。

### 决策 4: 宏观退出 —— 新增独立规则

**选择**: 在现有退出信号基础上新增一条：全球 risk appetite 达到极度贪婪（score > 80）且仓位已盈利 → 发出 `half_exit`。

**与现有退出的关系**: 宏观退出与 A 股自身退出（P30/P50）独立触发，取最激进的信号。如果宏观说 half_exit 但 A 股自身说 full_exit，以 full_exit 为准。

### 决策 5: 所有计算在 `_compute_triple_confirmation()` 同一位置完成

**选择**: 在 `golden_pit_service.py` 新增 `_parse_global_macro_overlay(gcf_data)` 方法，返回统一的数据结构。DCA service 消费这个结构做决策。

**理由**: GCF 数据已在三重确认处获取并缓存，在此处解析一次，输出标准化结构给 DCA 和 API 使用，避免重复请求和分散解析。

## Risks / Trade-offs

- [R: sentiment_score 不是真正的流动性数据] → 当前已有注释声明它为"风险偏好代理"，我们使用的阈值映射是合理的。若 ArkVol 后续暴露独立的闸门字段，从 agentic 字段中提取即可
- [R: 闸门硬停止可能导致错过反弹] → 闸门只阻止买入、不触发卖出。不会造成已持仓强平，只是不加仓
- [R: 全球宏观系数可能在震荡市中频繁翻转] → 使用 sentiment_score 的绝对区间而非单日变化，减少噪音

## Open Questions

无。所有设计决策已确定。
