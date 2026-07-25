## 1. 数据层：后端服务 + 模型

- [x] 1.1 新建 `backend/app/services/industry_leaderboard.py`，实现 `IndustryLeaderboardService` 类
- [x] 1.2 实现 `_get_industry_candidates()`：从 stock_pool.db 查询每行业市值前 3 名，过滤 ST/退市/日成交额<1亿
- [x] 1.3 实现 `_fetch_realtime_quotes_batch(symbols)`：腾讯 qt.gtimg.cn 批量获取价格/涨跌幅/换手率/成交额
- [x] 1.4 实现 `_fetch_indicators_batch(symbols)`：Tushare stk_factor_pro 批量获取 MA5/10/20/60、MACD DIF/DEA/histogram、ADX、PDI、MDI、RSI6、close、PE_TTM、vol、amount
- [x] 1.5 实现 `_fetch_daily_bars_batch(symbols)`：Tushare daily 批量获取近20日 OHLCV，用于量价分析和行业均涨幅计算；超时则降级为当日 volume_ratio 估算
- [x] 1.6 实现 `_detect_market_regime()`：从上证综指 000001.SH 的 ADX + MA5/MA20 判别趋势市/震荡市/过渡期
- [x] 1.7 实现 Round 1 四维评分方法：
  - `_compute_trend_composite(candidates, indicators, regime)`：MA排列层级(0-10/8) + MACD柱力度(0-10/7) + ADX趋势强度(0-8/7) + 趋势启动加分
  - `_compute_volume_price(candidates, indicators, daily_bars, regime)`：量价匹配度(0-7/8) + 突破放量比(0-5/6) + 缩量回调健康度(0-3/4)
  - `_compute_industry_relative_strength(candidates, quotes, daily_bars, regime)`：1日超额收益(0-7) + 5日累计超额(0-6/7) + 成交额贡献度(0-4/6)
  - `_compute_price_residual(candidates, quotes, indicators, regime)`：MA20乖离率倒U型(0-6/8) + 相对超额涨幅(0-6/7) + 非尾盘拉升验证(0-3)
- [x] 1.8 实现 Round 2 资金补算：
  - `_fetch_top10_moneyflow(top10_symbols)`：调用已有东方财富 `get_moneyflow` 逻辑，串行 HTTP 逐只获取 main_net/main_pct/d5_main_net（避免并发拦截）
  - `_compute_capital_persistence(candidates, moneyflows, regime)`：当日主力净流入/流通市值(0-10/8) + 主力净占比(0-8/7) + 5日累计/流通市值(0-7/7)
  - Top10 补上资金分后重排序；非 Top10 保持资金中性分（满分×50%）
- [x] 1.9 实现 `_apply_hard_filters(candidates, quotes)`：日成交额<1亿排除、一字板涨停标记、PE>200标记风险
- [x] 1.10 实现 `_apply_penalties(scored_candidates)`：维度地板惩罚(×0.7)、过热预警(RSI>90+乖离>15%扣3分)
- [x] 1.11 在 `backend/app/models/market.py` 新增 `LeaderboardItem`（含 capital_data 状态字段）、`LeaderboardResponse` Pydantic 模型
- [x] 1.12 实现 60 秒内存缓存（含资金数据）+ force_refresh 参数

## 2. API 层

- [x] 2.1 在 `backend/app/api/market.py` 新增 `GET /api/v1/market/industry-leaderboard` 端点
- [x] 2.2 支持查询参数：`limit`（默认 50）、`sort_by`（默认 composite_score）、`industry`（可选筛选）、`refresh`（强制刷新）
- [x] 2.3 Tencent 接口不可用时自动降级为 Tushare daily 表，标记 data_source
- [x] 2.4 daily 批量接口超时时量价维度降级为 volume_ratio 估算，标记 volume_data: "degraded"
- [x] 2.5 东方财富资金接口不可用时 Top10 资金取中性分，标记 capital_data: "unavailable"

## 3. 前端页面

- [x] 3.1 新建 `frontend/src/pages/IndustryLeaderboard.tsx` 排行表格页面
- [x] 3.2 表格列：排名、股票名称/代码、行业、涨跌幅、综合分、趋势分、资金分、量价分、行业强度分、价格分
- [x] 3.3 顶部显示当前市场状态标签（趋势市/震荡市/过渡期）
- [x] 3.4 行业下拉筛选器（从 API 返回的 industries_covered 动态生成）
- [x] 3.5 点击列头切换排序
- [x] 3.6 5 分钟自动刷新（setInterval 300000ms）+ 手动刷新按钮
- [x] 3.7 涨跌幅正负色（红涨绿跌）+ 综合分渐变色（高分暖色、低分冷色）
- [x] 3.8 过热预警/高估值风险/不可交易标记的视觉提示
- [x] 3.9 在 `frontend/src/api/client.ts` 新增 `getIndustryLeaderboard()` 函数
- [x] 3.10 在 `frontend/src/App.tsx` 新增路由 `/industry-leaderboard`
- [x] 3.11 导航栏新增入口

## 4. 验证

- [ ] 4.1 验证 API 返回 110 个行业均有候选（部分行业可能因过滤条件少于 3 只）
- [ ] 4.2 验证综合分范围在 0-100，各维度得分在合理区间
- [ ] 4.3 验证 Top10 资金分已补算且非中性值，排名 11+ 资金分为中性分
- [ ] 4.4 验证 60 秒缓存：连续两次调用间隔 < 60s 返回相同结果
- [ ] 4.5 验证腾讯降级：模拟腾讯接口不可用，自动切换到 Tushare 数据源
- [ ] 4.6 验证 daily 降级：模拟 daily 批量接口超时，量价维度降级为 volume_ratio 估算
- [ ] 4.7 验证资金降级：模拟东方财富接口不可用，Top10 资金取中性分，标记 capital_data: "unavailable"
- [ ] 4.8 验证市场状态判别：ADX>25 返回 trending，ADX<20 返回 ranging
- [ ] 4.9 验证硬过滤：ST/日成交额<1亿被排除，一字板涨停被标记
- [ ] 4.10 前端页面加载、筛选、排序、自动刷新、市场状态显示功能正常
