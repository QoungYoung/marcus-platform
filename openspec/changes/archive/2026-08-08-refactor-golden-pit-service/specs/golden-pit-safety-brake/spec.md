## ADDED Requirements

### Requirement: 状态数据提供前一交易日贪婪值

系统 SHALL 在每个指数的状态数据中提供 `prev_greed` 字段，其值 SHALL 为该指数前一交易日的贪婪值（与当日 `greed` 同源、同口径）。当历史序列长度 ≥2 时该字段 SHALL NOT 为 null，供 DCA 飞刀保护计算单日贪婪跌幅。

#### Scenario: 历史序列足够时 prev_greed 有值
- **WHEN** 某指数历史序列长度 ≥2
- **THEN** 状态数据中的 `prev_greed` SHALL 等于序列倒数第二天的贪婪值，且 SHALL NOT 为 null

#### Scenario: 飞刀保护依据 prev_greed 触发
- **WHEN** `prev_greed=0.380` 且当日 `greed=0.355`
- **THEN** DCA 飞刀保护 SHALL 触发并跳过当日买入，跳过原因 SHALL 标记为 `falling_knife`

