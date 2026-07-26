## Context

`IndustryLeaderboardService.get_leaderboard()` 当前仅支持实时模式：腾讯行情 + Tushare 日频指标 + 东方财富资金流。新增时间线功能后，需支持指定历史日期重建排行榜，并在弹窗中展示前瞻收益验证数据。

已有基础设施：
- `_60min_analysis.py` 已封装 `_call_rt_min_daily_single()` 通过 tu.brze.top 代理获取历史分钟K线
- `market.py:1206` 已有 `trade_cal` 调用模式，可复用获取交易日列表
- `IndustryLeaderboardService` 已具备 Tushare daily/stk_factor 批量查询能力

## Goals / Non-Goals

**Goals:**
- 排行榜页面支持 20 个历史交易日导航，点击日期切换排行榜数据
- 历史日期使用 Tushare 盘后数据重建（daily + stk_factor + moneyflow），不使用腾讯实时行情
- 弹窗在历史日期下展示前瞻收益（次日/3日/5日涨幅）+ 迷你 sparkline 曲线
- 历史日期排行榜永久缓存（数据不变），区别于实时模式的 60s TTL
- 分钟K线通过 tu.brze.top 代理获取，用于 sparkline 绘图

**Non-Goals:**
- 不支持未来日期（date > 最新交易日）
- 不修改现有评分公式和权重
- 不在时间线上显示非交易日
- 不改变现有实时模式的任何行为（date 参数为可选，不传时完全走原有逻辑）

## Decisions

### 1. 历史模式数据源切换策略

**决策**: `date` 参数传入时，`get_leaderboard()` 新增 `_historical_` 前缀方法替代实时方法。

| 维度 | 实时模式（date=None） | 历史模式（date 指定） |
|------|----------------------|----------------------|
| 行情 | 腾讯 qt.gtimg.cn | Tushare daily (close 作为 current_price) |
| 技术指标 | Tushare stk_factor (最新日) | Tushare stk_factor (该日期) |
| 日线 | Tushare daily (近20日) | Tushare daily (该日期前20日) |
| 资金流向 | 东方财富实时 (Top10) | Tushare moneyflow (该日期，仅 Top10) |
| 市场状态 | 上证综指最新 ADX | 上证综指该日期 ADX |

**理由**: 历史日期没有腾讯实时行情和东方财富实时资金流，Tushare 盘后数据是最可靠的历史数据源。stk_factor 和 daily 的批量查询方法已存在，只需调整日期参数。

**替代方案**: 全量预计算并存储到 SQLite。被拒绝——330 只股票 × 250 个交易日 × 5 个维度 ≈ 大量存储，且查询模式不确定（用户可能只看少数日期），按需重建更灵活。

### 2. 分钟K线获取方式

**决策**: 复用 `_60min_analysis.py` 中的 `_call_rt_min_daily_single()` 通过 tu.brze.top 代理获取。Sparkline 使用日线（daily 表）即可，分钟K线仅用于画更细腻的日内曲线。

**实际执行**: Sparkline 曲线使用 Tushare daily 日线数据（已批量获取），无需额外分钟K线调用。如果用户需要更高精度的日内曲线，再用 tu.brze.top 代理按需获取 stk_mins。当前 scope 先用日线画 sparkline。

**理由**: daily 数据已在历史排行榜重建时批量获取完毕（每个 symbol 20 个交易日 OHLCV），直接用这些数据画 sparkline 零额外成本。日线级别的 sparkline 对"前瞻验证"场景已足够。

### 3. 缓存策略

**决策**: 二级缓存 key = `{sort_by}:{industry}:{date}`：
- `date` 为最新交易日（实时模式）：60s TTL（现有行为不变）
- `date` 为历史日期：永久缓存（数据不变，无需过期）

**实现**: 缓存 dict 的 value 从 `Tuple[float, dict]` 改为 `Tuple[float, dict, Optional[str]]`，第三个字段标记 `date`。历史日期条目的 TTL 检查直接跳过。

### 4. 交易日获取

**决策**: 复用 `market.py:1206` 的 `trade_cal` 模式 — Tushare `pro.trade_cal(exchange='SSE', ...)` 获取交易日列表，回退方案为跳过周末的自然日推算。

前端时间线展示最近 20 个交易日（从最新交易日往前数），非交易日不在时间线上显示。

### 5. 前瞻收益计算

**决策**: 新端点 `GET /market/forward-returns/{symbol}?date=YYYYMMDD`

```
计算逻辑:
1. 查 daily 表获取 date 当日的 close（基准价）
2. 通过 trade_cal 获取 date 之后第 1/3/5 个交易日
3. 查 daily 表获取对应日期的 close
4. 涨幅 = (close_N - close_0) / close_0 × 100%
5. 返回 date 之后 10 个交易日的 close 数组用于 sparkline
```

**不分批查询**: 前端点击弹窗时按需请求，每次只查 1 只股票，延迟可接受（<200ms）。

### 6. Timeline UI 设计

**决策**: 横向滚动 pill 条，宽度占满页面内容区，位于标题下方、工具栏上方。

```
┌──────────────────────────────────────────────────────┐
│  ◀  │07/18│ │07/21│ │[07/22]│ │07/23│ │07/25│  ▶   │
│      ──────  ──────  ──────────  ──────  ──────      │
│                       SELECTED                       │
└──────────────────────────────────────────────────────┘
```

**交互**:
- 左右箭头滚动，当前选中 pill 高亮（`--bp-accent` 色）
- 点击 pill → `setSelectedDate(date)` → 触发 API 请求
- 尾部 "最新" pill 始终指向最新交易日，点击回到实时模式

**融入现有视觉**: 复用 Blue Archive DNA 的 `--bp-*` CSS 变量，pill 使用 `bp-stat-card` 类似的玻璃态 + 细边框风格。

### 7. 弹窗前瞻验证区块布局

**决策**: 在战斗参数卡片下方新增独立区块，仅在 `selectedDate < latestTradingDate` 时渲染。

```
┌─ 前瞻验证 · FORWARD VALIDATION ─────────────────────┐
│                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ 次日涨幅  │  │ 3日涨幅  │  │ 5日涨幅  │          │
│  │          │  │          │  │          │          │
│  │  +2.1%  │  │  +5.3%  │  │  +8.7%  │          │
│  │  🟢跑赢  │  │  🟢领跑  │  │  🟢卓越  │          │
│  └──────────┘  └──────────┘  └──────────┘          │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │  ▂▃▅▇█▇▆▄▃▂  ← sparkline (10日价格曲线)      │   │
│  │  07/22 07/23 07/24 07/25 07/28 ...           │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

## Risks / Trade-offs

- **[风险] 历史日期的 moneyflow 数据可能缺失** → 降级策略：Top10 资金标记为 "unavailable"，取中性分，与实时模式降级行为一致
- **[风险] trade_cal 接口限流** → 前端首次加载时由后端返回 `trading_days` 列表，避免前端频繁调用
- **[风险] 历史模式首次加载慢** → 批量 Tushare 查询已在实时模式验证（~330 只股票，3 个批量 API），历史模式延迟相当；永久缓存命中后毫秒级响应
- **[取舍] 分钟K线 sparkline vs 日线 sparkline** → 当前 scope 用日线数据画 sparkline（零额外成本）。如需更高精度日内曲线，后续可扩展分钟K线
