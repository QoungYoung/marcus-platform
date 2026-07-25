## Why

当前交易系统缺少一个"全局视野"页面：哪个行业的龙头正在走强？技术面和资金面都支持哪些标的？现有的 `concept-fund-flow` 接口只覆盖概念板块层面，`check_entry_filters` 只覆盖单股深度分析。需要一个横跨 110 个申万一级行业的龙头股实时排行页面，5 分钟刷新一次，用五维加权评分快速定位最强标的。

## What Changes

- **新增 API 端点** `GET /api/v1/market/industry-leaderboard`：返回按综合评分排行的行业龙头股列表
- **龙头识别**：每个申万一级行业取市值前 3 名（从 stock_pool.db 查），过滤 ST/退市，日成交额 < 1 亿排除
- **五维评分模型**：趋势综合(22-28%) + 资金持续性(22-25%) + 量价配合(15-18%) + 行业相对强度(17-20%) + 价格残差(15-18%)，ADX 自动判别趋势市/震荡市切换权重
- **两轮评分架构**：
  - Round 1：全量 330 只候选股 4 维评分（3 次批量 API 调用：腾讯实时行情 + Tushare stk_factor_pro + Tushare daily）
  - Round 2：Top10 补算资金维度（东方财富实时接口，10 次并行 HTTP），重排序
  - 非 Top10 资金分取中性值，不影响全量排序公平性
- **前端页面**：React 排行榜表格，支持按行业筛选、按单项分排序，5 分钟自动刷新
- 技术指标使用昨日盘后确认值（stk_factor_pro），辅以盘中实时涨跌幅和实时资金流向补足时效

## Capabilities

### New Capabilities
- `industry-leaderboard`: 申万一级行业龙头股的实时排行系统，含五维加权评分模型（趋势/资金/量价/行业相对强度/价格残差）、两轮评分架构、批量数据获取、前端排行页面

### Modified Capabilities
<!-- No existing capabilities modified -->

## Impact

- `backend/app/api/market.py` — 新增 `get_industry_leaderboard` 端点
- `backend/app/services/industry_leaderboard.py` — 新建，龙头筛选 + 两轮评分计算 + 市场状态判别 + 缓存逻辑
- `backend/app/models/market.py` — 新增 `LeaderboardResponse` / `LeaderboardItem` Pydantic 模型
- `frontend/src/pages/IndustryLeaderboard.tsx` — 新建，排行表格页面
- `frontend/src/api/market.ts` — 新增前端 API 调用函数
- `frontend/src/router.tsx` — 新增路由
- `core/stock_pool_manager.py` — 已有 `stock_pool` 表和 `market_cap` 字段，已修复市值数据
