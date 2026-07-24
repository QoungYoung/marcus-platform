## Why

2026-07-22 紫金矿业建仓时 RSI6=83.9, KDJ-J=102.8, CCI=250.2 三重极端超买，第三层过滤却全部判为"正常"直接放行，导致建仓在阶段顶部，次日起 -4.69% 暴跌。根因有三：RSI6 和 KDJ-J 的"正常/警告/禁止"阈值设得过高，CCI 指标完全缺失，且无多指标共振判定。

## What Changes

- **新增 CCI 超买超卖过滤**：在第三层中新增 CCI（商品通道指数）判定，设定分档阈值
- **新增多指标共振检测**：当 RSI6、KDJ-J、CCI 三者中有 ≥2 个处于"警告"或更高等级时，触发联合降级
- **收紧 RSI6 阈值**：当前"正常"上限 85 → 75，"仅试探仓" 85-90 → 75-85，"禁止建仓" 90 → 85
- **收紧 KDJ-J 阈值**：当前"正常"上限 105 → 95，"仅试探仓" 105-110 → 95-105，"禁止建仓" 110 → 105
- **Layer 3 判定逻辑重构**：由原来的"各自为政"改为"最差指标决定等级 + 共振降一级"的联合判定

## Capabilities

### New Capabilities
- `entry-filter-overbought`: 入场过滤第三层超买判定（含 CCI 指标、多指标共振检测、收紧后的阈值表）

### Modified Capabilities
<!-- No existing specs cover the entry filter overbought logic in detail -->

## Impact

- **受影响的代码**：`backend/app/api/indicator.py` — `check_entry_filters` 函数中 Layer 3 判定逻辑（约第 2284-2358 行）
- **受影响的 API**：`POST /api/v1/indicator/check-entry-filters` — 返回体 `layer3_overbought` 字段结构不变，但判定逻辑变化
- **下游影响**：`candidate_pool_monitor.py`、`trade_graph.py` 中的入场过滤调用 — 无需改动，仅消费 API 返回结果
- **无破坏性变更**：API 接口签名和返回结构不变，仅内部判定规则更严格
