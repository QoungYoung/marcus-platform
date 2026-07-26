## 1. 后端：历史模式数据层

- [x] 1.1 `get_leaderboard()` 新增 `date` 参数，当 date 传入时走历史模式分支（跳过腾讯行情、东方财富资金流），date 为空时保持现有实时逻辑不变
- [x] 1.2 新增 `_historical_quotes(symbols, date)` 方法：从 Tushare daily 表获取指定日期的 close/change_pct/amount 等字段，组装为与 `_fetch_realtime_quotes_batch` 相同格式的 dict
- [x] 1.3 新增 `_historical_indicators(symbols, date)` 方法：从 Tushare stk_factor_pro 获取指定日期之前的数据，计算当日 MA/ADX/MACD/RSI，复用现有 `_calc_ma` 和 `_calc_adx`
- [x] 1.4 `_fetch_daily_bars_batch` 支持 `end_date` 参数（历史模式下传入指定日期而非 today）
- [x] 1.5 新增 `_historical_moneyflow(top10_symbols, date)` 方法：从 Tushare moneyflow 表获取指定日期的资金流向，降级时返回 None（标记 unavailable）
- [x] 1.6 缓存策略升级：缓存 key 改为 `{sort_by}:{industry}:{date}`，历史日期（date 非空）永久缓存跳过 TTL 检查

## 2. 后端：API 层

- [x] 2.1 `GET /market/industry-leaderboard` 新增可选 `date` 查询参数（`YYYYMMDD` 格式），传递至 service 层
- [x] 2.2 新增 `GET /market/forward-returns/{symbol}?date=YYYYMMDD` 端点：通过 trade_cal 获取 date 之后的第 1/3/5 个交易日，查 daily 表计算涨幅；返回 10 日 close 数组用于 sparkline
- [x] 2.3 `forward-returns` 端点处理边界情况：date 为最新交易日时返回 `available: false`；symbol 无数据时返回空；非交易日 date 返回错误提示
- [x] 2.4 在 `backend/app/models/market.py` 新增 `ForwardReturnsResponse` Pydantic 模型

## 3. 前端：时间线组件

- [x] 3.1 在 `IndustryLeaderboard.tsx` 新增 `selectedDate` state，null 表示实时模式（默认），非 null 时切换到历史日期
- [x] 3.2 新增 `TimelineBar` 组件：横向滚动 pill 条，显示最近 20 个交易日（从 API 首次返回时提取 trading_days 列表），当前选中 pill 使用 `--bp-accent` 色高亮
- [x] 3.3 Timeline 交互：点击 pill → `setSelectedDate(date)` → 触发 API 重新请求（传入 date 参数）；尾部 "最新" pill 回到实时模式（date=null）
- [x] 3.4 后端 `GET /market/industry-leaderboard` 返回中新增 `trading_days` 字段（最近 20 个交易日列表），前端首次加载时获取
- [x] 3.5 `marketApi.getIndustryLeaderboard()` 新增 `date` 参数；新增 `marketApi.getForwardReturns(symbol, date)` 方法

## 4. 前端：弹窗前瞻验证

- [x] 4.1 弹窗中新增条件渲染：`selectedDate` 非 null 且小于 `trading_days[0]`（最新交易日）时，在战斗参数卡片下方渲染前瞻验证区块
- [x] 4.2 调用 `getForwardReturns()` 获取前瞻数据，展示 3 个指标卡片（次日涨幅、3日涨幅、5日涨幅），正负值分色
- [x] 4.3 使用日线 close 数组渲染迷你 sparkline 曲线（纯 SVG polyline，无需第三方图表库）
- [x] 4.4 前瞻验证区块使用现有 Blue Archive DNA 样式（`--bp-*` 变量、玻璃态卡片、corner brackets 装饰）

## 5. 验证

- [x] 5.1 验证历史日期 API 返回数据与实时模式格式一致，`data_source` 为 "tushare"
- [x] 5.2 验证 forward-returns 端点返回的次日/3日/5日涨幅与 daily 表数据一致
- [x] 5.3 验证历史日期缓存：连续两次相同 date 请求，第二次无 Tushare API 调用
- [x] 5.4 验证时间线切换：点击 pill → 排行榜刷新为对应日期数据 → 弹窗显示前瞻收益
- [x] 5.5 验证实时模式不受影响：不传 date 参数时，排行榜行为与改动前一致
