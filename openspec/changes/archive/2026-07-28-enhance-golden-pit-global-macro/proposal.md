## Why

黄金坑 DCA 策略目前只看 A 股内部信号（贪婪值分位、拐点确认、指数共振），完全忽略全球宏观环境。当美元流动性收紧或全球风险偏好恶化时，A 股黄金坑可能是系统性风险而非独立机会——在闸门收紧时继续定投等于接飞刀。全球资金流 API 的 `sentiment_score` 已在三重确认中获取但仅做布尔判断，其余数据（流动性闸门、趋势、五市场横向对比）全部丢弃。将这层数据用起来，用极小改动换取显著的风险收益比提升。

## What Changes

- **流动性闸门硬止损**: DCA 买入入口新增美元美债流动性闸门检查，闸门收紧时跳过所有买入
- **全球趋势拐点验证**: 拐点确认时交叉验证全球风险偏好趋势，过滤假拐点信号
- **仓位系数动态调节**: `resonance_multiplier` 后叠加全球宏观系数，恐慌时降仓、共振时加仓
- **退出信号宏观增强**: 全球风险偏好极端贪婪时提前止盈，不等待 P50

## Capabilities

### New Capabilities
- `golden-pit-global-macro`: 黄金坑策略的全球宏观叠加层——流动性闸门、全球风险偏好趋势、动态仓位系数、宏观退出信号

### Modified Capabilities
- `golden-pit-exit`: 新增全球风险偏好极端贪婪时提前止盈规则
- `golden-pit-per-index-params`: 新增 `global_macro_coefficient` 参数支持

## Impact

- `backend/app/services/golden_pit_service.py` — 三重确认中解析流动性闸门 & 全球趋势
- `backend/app/services/golden_pit_dca_service.py` — 买入入口闸门检查、仓位系数叠加、退出信号扩展
- `backend/app/api/golden_pit.py` — API 响应中暴露宏观层数据
- 数据库: 无需迁移，系数为内存计算
