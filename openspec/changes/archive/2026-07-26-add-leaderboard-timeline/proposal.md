## Why

行业龙头排行目前只能查看当日实时数据，用户无法回溯历史某一天的排行榜表现，也无法验证评分模型对后续涨幅的预测能力。新增时间线功能让用户可以切换到任意历史交易日查看当日排行，并在弹窗中展示该日之后的前瞻收益数据（次日/3日/5日涨幅+迷你K线曲线），用于评估评分系统的预测有效性。

## What Changes

- 后端 `GET /market/industry-leaderboard` 新增 `date` 查询参数，传入日期时切换到历史模式（全部使用 Tushare 历史数据重建排行榜，含日线行情、日频技术指标、日频资金流向）
- 后端新增 `GET /market/forward-returns/{symbol}` 端点，返回指定日期之后的前瞻收益（次日/3日/5日涨幅 + 10日分钟K线用于画迷你曲线）
- 历史日期数据启用永久缓存（历史数据不变，无需 TTL 过期），实时数据保持现有 60s 缓存
- 前端新增水平时间线条（最近 20 个交易日 pill 导航），点击切换日期 → 重新请求该日排行榜
- 前端弹窗在选中历史日期时，战斗参数卡片下方新增"前瞻验证"区块：3 个指标卡片（次日/3日/5日涨幅）+ 迷你 sparkline 曲线
- 分钟K线数据通过 tu.brze.top 代理获取（已有 `_call_rt_min_daily_single` 实现），用于绘制迷你价格曲线

## Capabilities

### New Capabilities
- `leaderboard-timeline`: 时间线日期导航 + 历史排行榜回溯 + 前瞻收益验证（次日/3日/5日涨幅 + 迷你K线曲线）。历史数据全部来自 Tushare 盘后数据（daily + stk_factor + moneyflow），分钟K线通过 tu.brze.top 代理获取。

### Modified Capabilities
<!-- None - 现有排行榜实时功能不受影响，date 参数为可选 -->

## Impact

- **后端 API**: `GET /market/industry-leaderboard` 新增可选 `date` 参数；新增 `GET /market/forward-returns/{symbol}?date=YYYYMMDD` 端点
- **后端 Service**: `IndustryLeaderboardService.get_leaderboard()` 新增 `date` 参数 + 历史模式分支；新增 `_historical_quotes()`、`_historical_indicators()` 方法；新增 `get_forward_returns()` 方法
- **后端缓存**: 新增二级缓存 key（`{sort_by}:{industry}:{date}`），历史日期缓存永久有效
- **前端页面**: `IndustryLeaderboard.tsx` 新增 TimelineBar 组件、date 状态管理、API date 参数传递
- **前端弹窗**: 新增 ForwardValidation 区块（条件渲染：`selectedDate < latestTradingDate`）
- **前端 API client**: `getIndustryLeaderboard()` 新增 `date` 参数；新增 `getForwardReturns()` 方法
