## Why

当前交易 AI 的主线选择仅依赖单日概念板块数据（当日主力资金 TOP5 ∩ 当日涨幅 TOP5），在震荡市中单日数据容易被一日游板块诱杀。需要增加 5 日持续性维度形成"双轨"主线确认——明线抓当日爆发力，暗线抓 5 日蓄力趋势，二者平等，让 AI 不再遗漏电力、红利等慢牛板块。

## What Changes

- **新增后端 API** `GET /api/v1/market/concept-fund-flow-5d`：基于 Tushare `moneyflow_ind_dc` 查询 5 个交易日数据，按概念聚合计算 5日累计涨跌幅、上涨天数、5日累计资金，结合当日实时门控（主力净流入 > 0），输出暗线综合评分（涨跌幅排名分 × 0.5 + 上涨天数排名分 × 0.5）
- **新增前端工具** `get_concept_fund_flow_5d`：在 pi-server tools.ts 中注册，供 AI 调用获取暗线评分排行
- **修改交易 Prompt**：在 `TRADE_SYSTEM_PROMPT`（index.ts + prompt_seeds.py 两处）将选股逻辑从"单日主线确认"替换为"双轨主线确认"，新增三等优先级（双轨共振 > 暗线高分 > 明线独有）
- **修改聊天 Prompt**：在 `CHAT_SYSTEM_PROMPT` 工具列表中补充 `get_concept_fund_flow_5d` 条目

## Capabilities

### New Capabilities
- `sector-5d-aggregation`: 5 日概念板块资金流向聚合查询，后端按概念聚合 Tushare 日频数据计算累计涨跌幅和上涨天数，结合当日实时门控输出暗线综合评分
- `dual-track-sector-selection`: 交易 AI 双轨主线选择逻辑，明线（当日动量）与暗线（5 日持续性）平等权重，三等优先级选股策略

### Modified Capabilities
<!-- No existing spec requirements are changing. Existing tools and endpoints remain untouched. -->

## Impact

- `backend/app/api/market.py`：新增约 80 行 API 端点
- `servers/pi-server/src/tools.ts`：新增约 50 行工具定义 + 注册
- `servers/pi-server/src/index.ts`：Prompt 替换约 50 行
- `backend/app/db/prompt_seeds.py`：Prompt 替换约 50 行
- 不涉及数据库迁移、不涉及破坏性变更、不影响已有 API
