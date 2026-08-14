---
name: marcus-panel-tools
description: Marcus 交易系统复盘（reflect）模式的 21 个只读数据查询工具，用于复盘分析、周度反思、专家组讨论和个股买卖建议，覆盖行情、技术指标、资金流向、仓位计算、黄金坑评分与DCA、Pi 分析历史、交易报告、数据库查询等（HTTP API 调用，无交易执行权限）。
---

# Marcus 专家组群聊模式工具集

## 概述

本 skill 描述 Marcus 交易系统 **Pi Server 群聊模式（reflect 模式）** 提供给 AI 专家组的 21 个只读工具（含黄金坑评分与 DCA 定投查询）。这些工具对应 `servers/pi-server/src/tools.ts` 中的 `REFLECT_TOOLS` 数组，用于复盘分析、周度反思、专家组群聊讨论等场景。

**特点**：
- 全部只读，无交易执行权限（不含 `place_order` / `cancel_order`）
- 主要使用 Tushare 历史盘后数据，避免未来函数
- 通过 HTTP API 调用后端服务

**接口基础地址**：`http://81.70.44.68/api/v1/`

**调用约定**：
- 所有请求返回 JSON
- 股票代码格式：`SH600519` / `SZ000001` / 纯数字 `600519` 均可
- 日期格式：`YYYYMMDD`（如 `20240524`）或 `YYYY-MM-DD`（如 `2024-05-24`，部分接口）
- 失败响应含 `error` 字段

---

## 工具清单（21 个）

| # | 工具名 | 用途 | API 端点 |
|---|--------|------|----------|
| 1 | get_daily_kline_qfq | 日K线(前复权) | `GET /market/pro-bar/{symbol}` |
| 2 | get_technical | 技术指标(盘后确认) | `GET /market/technical/{symbol}` |
| 3 | get_moneyflow | 资金流向 | `GET /market/moneyflow/{symbol}` |
| 4 | get_realtime_indicators | 实时技术指标(盘中估算) | `GET /indicator/realtime/{symbol}` |
| 5 | get_fibonacci_levels | 斐波那契回撤 | `POST /indicator/fibonacci` |
| 6 | get_daily_channel | 日内K值通道 | `GET /indicator/daily-channel/{symbol}` |
| 7 | get_trade_advice | 综合操作建议 | `POST /indicator/advice` |
| 8 | calc_position | 仓位计算 | `POST /indicator/calc-position` |
| 9 | check_entry_filters | 入场三层过滤 | `POST /indicator/check-entry-filters` |
| 10 | get_fina_mainbz | 主营业务构成 | `GET /indicator/fina-mainbz/{symbol}` |
| 11 | get_express | 业绩快报 | `GET /indicator/express/{symbol}` |
| 12 | get_pi_analysis_history | Pi分析历史 | `GET /scan/pi-analysis` |
| 13 | get_trade_history | 交易报告历史 | `GET /scan/trade-reports` |
| 14 | get_latest_scan_report | 最新扫描报告 | `GET /scan/latest` |
| 15 | read_db_table | 数据库查询 | `GET /db/query` |
| 16 | get_db_schema | 数据库结构 | `GET /db/schema/{db}` |
| 17 | get_golden_pit_status | 黄金坑状态总览 | `GET /golden-pit/status` |
| 18 | get_golden_pit_history | 贪婪值历史走势 | `GET /golden-pit/history` |
| 19 | get_golden_pit_dca_status | 黄金坑DCA状态 | `GET /golden-pit/dca/status` |
| 20 | get_golden_pit_dca_logs | 黄金坑DCA日志 | `GET /golden-pit/dca/logs` |
| 21 | get_golden_pit_etf_configs | 黄金坑ETF配置 | `GET /golden-pit/etf-configs` |

---

## 1. get_daily_kline_qfq — 日K线(前复权)

**用途**：获取前复权日K线，除权除息日无价格跳空缺口，均线/MACD/RSI 等技术指标连续可靠。复盘分析专用。

**数据源**：Tushare pro_bar（盘后数据）

**请求**：`GET /market/pro-bar/{symbol}?adj=qfq&start_date={YYYYMMDD}&end_date={YYYYMMDD}&limit={n}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码，如 `SH600519` |
| adj | string | 是 | 固定 `qfq`（前复权） |
| start_date | string | 否 | 开始日期 `YYYYMMDD`，默认 90 天前 |
| end_date | string | 否 | 结束日期 `YYYYMMDD`，默认今天 |
| limit | number | 否 | 返回条数上限，默认 100，最大 500 |

**返回**：`{ symbol, bars: [{ trade_date, open, close, high, low, vol, amount }] }`

---

## 2. get_technical — 技术指标(盘后确认)

**用途**：获取 MACD/KDJ/RSI/布林带等 60+ 技术因子。⚠️ 返回的是最近收盘日的**已确认值**，不是当日盘中值。

**数据源**：Tushare stk_factor_pro（盘后数据，基于收盘价计算）

**请求**：`GET /market/technical/{symbol}?start_date={YYYYMMDD}&end_date={YYYYMMDD}&limit={n}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| start_date | string | 否 | 默认 90 天前 |
| end_date | string | 否 | 默认今天 |
| limit | number | 否 | 默认 100，最大 500 |

**返回**：`{ symbol, data: [{ trade_date, close, macd_dif, macd_dea, macd, kdj_k, kdj_d, kdj, rsi_6, rsi_12, rsi_24, boll_upper, boll_mid, boll_lower, cci, wr }] }`（按日期倒序）

**信号判定**（可自行计算）：
- MACD 金叉：前一日 `macd_dif < macd_dea`，当日 `macd_dif >= macd_dea`
- KDJ 超买：`kdj >= 80`；超卖：`kdj <= 20`
- RSI6 超买：`>= 70`；超卖：`<= 30`

---

## 3. get_moneyflow — 资金流向

**用途**：获取个股资金流向（主力/超大单/大单/中单/小单净流入 + 5日/10日累计），判断主力动向。

**数据源**：东方财富实时 / 同花顺即时 / Tushare 日频降级

**请求**：`GET /market/moneyflow/{symbol}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |

**返回**（东方财富源）：
```json
{
  "symbol": "SH600519", "name": "贵州茅台",
  "price": 1750.0, "change_pct": 1.5, "turnover_rate": "0.5",
  "source": "eastmoney",
  "main_net": 12345678, "main_pct": "10.5",
  "lg_net": ..., "md_net": ..., "sm_net": ..., "xs_net": ...,
  "d5_main_net": ..., "d10_main_net": ...
}
```

---

## 4. get_realtime_indicators — 实时技术指标(盘中估算)

**用途**：获取盘中实时估算的 KDJ/MACD/RSI/MA。⚠️ `data_source="intraday_estimate"`（盘中估算），今日高低点未最终确认，**仅作辅助参考，不能作为独立建仓的唯一理由**。

**数据源**：腾讯 `qt.gtimg.cn` 实时行情 + Tushare 历史日线计算

**请求**：`GET /indicator/realtime/{symbol}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |

**返回**：
```json
{
  "symbol": "SH600519", "name": "贵州茅台",
  "realtime": {
    "current_price": 1750.0, "calc_time": "10:30:00",
    "data_source": "intraday_estimate",
    "kdj_k": 75.2, "kdj_d": 70.1, "kdj_j": 85.4,
    "macd_dif": 1.2345, "macd_dea": 1.1000, "macd_bar": 0.1345,
    "rsi_6": 65.3, "rsi_12": 58.2, "rsi_24": 55.1,
    "ma5": 1740.0, "ma10": 1730.0, "ma20": 1700.0
  },
  "historical": [{ "trade_date": "...", ... }]  // 最近3日盘后确认值
}
```

---

## 5. get_fibonacci_levels — 斐波那契回撤

**用途**：计算 0.382/0.618/0.786 回撤价位，判断支撑/阻力位和当前价格所处区间。用于右侧交易寻找入场点和止损位参考。

**请求**：`POST /indicator/fibonacci`

**Body**：
```json
{ "symbol": "SH600519", "high": 1800.0, "low": 1600.0 }
```
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| high | number | 否 | 阶段顶部价格（不传则从 90 天 K 线自动提取） |
| low | number | 否 | 阶段底部价格（不传则自动提取） |

**返回**：
```json
{
  "symbol": "SH600519", "high": 1800, "low": 1600, "diff": 200,
  "current_price": 1750,
  "levels": [
    { "ratio": 0.382, "price": 1723.6, "label": "常规买点" },
    { "ratio": 0.618, "price": 1676.4, "label": "强防生死线" },
    { "ratio": 0.786, "price": 1642.8, "label": "深坑/放弃" }
  ],
  "position_zone": "0.382上方·强势", "zone_suggestion": "..."
}
```

---

## 6. get_daily_channel — 日内K值通道

**用途**：计算日内压力/支撑通道（基于 K=0.98848 常数）。压力线=分时均价/K，支撑线=分时均价×K。用于判断日内超短线交易的精确入场/离场价位。

**请求**：`GET /indicator/daily-channel/{symbol}?avg_price={price}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| avg_price | number | 否 | 分时均价（不传则从行情估算） |

**返回**：
```json
{
  "symbol": "SH600519", "constant_k": 0.98848,
  "avg_price": 1745.0, "current_price": 1750.0,
  "top_line": 1765.4, "bottom_line": 1724.8,
  "channel_width_pct": 2.33, "position": "通道中上"
}
```

---

## 7. get_trade_advice — 综合操作建议

**用途**：获取完整的股票操作建议（牛股计算器决策树）。判断该买入/持有/卖出。结合斐波那契回撤、K值通道、时间证伪、破底止损等规则。`cost` 有值→持仓模式，不传→观察模式。

**触发场景**：用户问"这只股票怎么看""该买还是该卖""现在什么建议""帮我分析持仓"。

**请求**：`POST /indicator/advice`

**Body**：
```json
{
  "symbol": "SH600519",
  "cost": 1700.0,
  "high": 1800.0,
  "low": 1600.0,
  "avg_price": 1745.0,
  "buy_date": "2024-05-01"
}
```
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| cost | number | 否 | 成本价（有持仓时传入，触发持仓模式） |
| high | number | 否 | 阶段顶部（不传则自动提取） |
| low | number | 否 | 阶段底部（不传则自动提取） |
| avg_price | number | 否 | 分时均价（不传则用当前价估算） |
| buy_date | string | 否 | 建仓日期 `YYYY-MM-DD`（有持仓时传入） |

**返回**：`{ symbol, name, current_price, change_pct, mode, signal, signal_class, signal_details, risk_flags, fib_382, fib_618, fib_786, k_channel_top, k_channel_bottom, ... }`

`signal_class` 取值：`danger`🔴 / `warning`🟡 / `gold`🏆 / `blue`🔵 / `cyan`🩵 / `normal`⚪

---

## 8. calc_position — 仓位计算

**用途**：【建仓前必调】根据信号强度、产业链角色、加仓层级、市场立场，综合计算建议仓位数量、止损价位和风险验证。自动拉取账户状态、当前价格、近5日振幅、大盘涨跌幅。

**请求**：`POST /indicator/calc-position`

**Body**：
```json
{
  "symbol": "SH600519",
  "signal_strength": "high",
  "chain_role": "upstream",
  "tier": "probe",
  "stance": "green"
}
```
| 参数 | 类型 | 必填 | 取值 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| signal_strength | string | 是 | `low`(低确定性/单一信号) / `medium`(2指标共振) / `high`(3+指标共振+板块龙头+主力净流入) |
| chain_role | string | 是 | `upstream`(上游核心) / `mid`(中游配套) / `downstream`(下游应用) |
| tier | string | 是 | `probe`(试探仓/首仓) / `confirm`(确认仓/需浮盈≥1%) / `sprint`(冲刺仓/需浮盈≥3%) |
| stance | string | 是 | `green`(激进/总仓≤60%) / `yellow`(谨慎/总仓≤50%) / `red`(观望/总仓≤20%) |

**返回**：
```json
{
  "symbol": "SH600519", "name": "贵州茅台",
  "total_asset": 100000, "available_cash": 50000, "position_ratio": 50.0,
  "signal_strength": "high", "chain_role": "upstream", "tier": "probe", "stance": "green",
  "single_stock_cap_pct": 25, "role_cap_pct": 15, "total_cap_pct": 60,
  "amplitude": 3.5, "amplitude_tier": "中波",
  "quantity": { "max_shares": 100, "rec_shares": 50, "rec_pct": 8.75, "probe_shares": 20, "probe_pct": 3.5 },
  "stop_loss": { "dynamic_stop_pct": -2.0, "hard_stop_price": 1680.0, "iron_rule2_t1_pct": 2, "iron_rule2_t2_pct": 5, ... },
  "validation": { "single_cap_ok": true, "total_position_ok": true, "cash_reserve_ok": true, "max_loss_ok": true, ... },
  "warnings": [],
  "all_pass": true
}
```

---

## 9. check_entry_filters — 入场三层过滤

**用途**：【建仓前必调】对标的执行三层过滤：技术面 → 主力行为 → 超买过滤。返回逐层判定(✅/⚠️/🚫) + 降仓系数 + 买入确认规则(涨幅分段)。

**请求**：`POST /indicator/check-entry-filters`

**Body**：
```json
{
  "symbol": "SH600519",
  "sector_net_inflow": 50000000,
  "volume_ratio": 1.8
}
```
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| sector_net_inflow | number | 否 | 所属板块主力资金净流入（元），用于 MA5<MA20 时的备用检查 |
| volume_ratio | number | 否 | 量比，已知可传入，否则从行情估算 |

**返回**：
```json
{
  "symbol": "SH600519", "name": "贵州茅台", "current_price": 1750,
  "tech": { "current_price": 1750, "ma5": 1740, "ma20": 1700, "macd_status": "金叉", "rsi6": 65, "kdj_j": 85, "rsr": 1.2, "intraday_percentile": 60, "capital_efficiency": 1.5 },
  "layer1_tech":    { "grade": "✅", "pass": true, "details": [...] },
  "layer2_capital": { "grade": "✅", "pass": true, "details": [...] },
  "layer3_overbought": { "grade": "✅", "pass": true, "details": [...] },
  "buy_confirmation": { "change_pct": 1.5, "action": "直接入场", "wait_minutes": 0, "allow": true, "ratio": 1.0 },
  "final_decision": "✅ 可建仓",
  "downgrade_multiplier": 1.0, "max_position_pct": 25,
  "summary": "..."
}
```

**三层过滤说明**：
- **第一层·技术面**：MA5/MA20、MACD金叉/DIF收敛、RSR、日内分位、资金效率
- **第二层·主力行为**：今日/5日/10日主力净流入、小单流向
- **第三层·超买过滤**：RSI6(≥70🚫/正常🟢)、KDJ-J(≥110🚫)

**买入确认规则**（按涨幅分段）：
- `< 3%` → 直接入场
- `3-5%` → 等 3-5 分钟
- `5-8%` → 等 2-3 分钟 + 量比>1.5
- `> 8%` → 放弃

---

## 10. get_fina_mainbz — 主营业务构成

**用途**：获取个股主营业务构成（产品/行业/地区维度的收入与利润占比），分析公司核心收入来源、产业链定位。

**数据源**：Tushare fina_mainbz

**请求**：`GET /indicator/fina-mainbz/{symbol}?period={YYYYMMDD}&limit={n}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| period | string | 否 | 报告期 `YYYYMMDD`，如 `20231231`，默认最新 |
| limit | number | 否 | 返回条数，默认 10，最大 50 |

**返回**：`{ symbol, report_period, data_source, records: [{ type: "P"|"I"|"R", bz_item, bz_sales, bz_profit, bz_cost }] }`

`type` 含义：`P`=产品 / `I`=行业 / `R`=地区

---

## 11. get_express — 业绩快报

**用途**：获取个股业绩快报（营收/利润/EPS/ROE/同比增长），判断基本面强弱。

**数据源**：Tushare express

**请求**：`GET /indicator/express/{symbol}?period={YYYYMMDD}&limit={n}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| symbol | string | 是 | 股票代码 |
| period | string | 否 | 报告期 `YYYYMMDD`，默认最近报告期 |
| limit | number | 否 | 返回期数，默认 5，最大 50 |

**返回**：`{ symbol, data_source, records: [{ end_date, revenue, yoy_revenue, n_income, yoy_n_income, basic_eps, weighted_roe, operate_profit, yoy_operate_profit }] }`

---

## 12. get_pi_analysis_history — Pi分析历史

**用途**：按日期范围查询整周 Pi 分析历史记录。返回每天每轮扫描的 Pi 策略分析，含 stance（立场）、position_limit（仓位上限）、reason（判断理由）和完整 report。用于周度反思时回顾整周策略演变。

**请求**：`GET /scan/pi-analysis?start_date={YYYY-MM-DD}&end_date={YYYY-MM-DD}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| start_date | string | 否 | 开始日期 `YYYY-MM-DD`，默认本周一 |
| end_date | string | 否 | 结束日期 `YYYY-MM-DD`，默认今天 |

**返回**：
```json
{
  "date_range": { "start": "2024-05-20", "end": "2024-05-24" },
  "days_count": 5, "total_records": 15,
  "records": [{ "date": "2024-05-20", "timestamp": "...", "task_name": "早盘", "stance": "green", "position_limit": 60, "reason": "...", "report": "..." }]
}
```

---

## 13. get_trade_history — 交易报告历史

**用途**：按日期范围查询整周 Pi 交易执行报告。返回每天每次交易窗口的完整报告，含买卖决策、仓位变化、产业链组合逻辑、风险监控。用于评估策略执行质量，对比交易动作与 Pi 分析的一致性。

**请求**：`GET /scan/trade-reports?start_date={YYYY-MM-DD}&end_date={YYYY-MM-DD}`

**参数**：同上（start_date / end_date）

**返回**：
```json
{
  "date_range": { "start": "...", "end": "..." },
  "days_count": 5, "total_records": 8,
  "records": [{ "date": "...", "timestamp": "...", "task_id": "morning", "stance": "green", "position_limit": 60, "reason": "...", "report": "..." }]
}
```

`task_id` 含义：`morning`=早盘 / `late`=午前 / `afternoon`=午后 / `closing`=尾盘

---

## 14. get_latest_scan_report — 最新扫描报告

**用途**：获取最新的盘中扫描报告，含 market_stance / position_limit / hot_concepts / watchlist / 系统报告 / pi_analysis（Pi 预消化策略建议）。

**请求**：`GET /scan/latest?date={YYYY-MM-DD}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| date | string | 否 | 日期 `YYYY-MM-DD`，默认今天 |

**返回**：
```json
{
  "timestamp": "...", "market_stance": "green", "position_limit": 60,
  "hot_concepts": ["有色", "电力设备"],
  "watchlist": ["SH600519"],
  "report": "...(系统扫描报告)",
  "pi_analysis": { "stance": "green", "position_limit": 60, "reason": "...", "report": "..." }
}
```

---

## 15. read_db_table — 数据库查询

**用途**：读取数据库表的数据，支持查询、筛选和排序。

**可用数据库**：`stock_pool.db`、`trades.db`、`news.db`、`cache.db`

**请求**：`GET /db/query?db={db}&table={table}&columns={cols}&where={cond}&order_by={order}&limit={n}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| db | string | 是 | 数据库名 |
| table | string | 是 | 表名 |
| columns | string | 否 | 要查询的列，逗号分隔 |
| where | string | 否 | WHERE 条件 |
| order_by | string | 否 | 排序，如 `change_pct DESC` |
| limit | number | 否 | 返回条数，默认 100 |

**返回**：`{ rows: [...] }`

**常用查询示例**（stock_pool.db）：
- 查某股票概念：`table=stock_concept_map&where=ts_code LIKE '000001%'`
- 查某概念成分股：`table=stock_concept_map&where=concept_name = '半导体概念'&limit=50`

`ts_code` 格式为 `代码.交易所`（如 `000001.SZ`），`symbol` 为纯数字代码（如 `000001`）。

---

## 16. get_db_schema — 数据库结构

**用途**：获取数据库的表结构和字段信息。

**请求**：`GET /db/schema/{db}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| db | string | 是 | 数据库名：`stock_pool` / `trades` / `news` / `cache` |

**返回**：`{ schema: [...] }`

---

## 17. get_golden_pit_status — 黄金坑状态总览

**用途**：获取逐指数黄金坑评分状态（golden_pit / warning / normal）、窗口信息、三重确认、预测与全球宏观摘要，用于复盘判断当前是否处于黄金坑区域、哪些指数已入坑/预警。

**数据源**：ArkVol ai-summary / 全球资金流 + 本地 DB 快照（日频，2 小时 TTL 缓存）

**请求**：`GET /golden-pit/status`

**参数**：无

**返回**：`{ code: 0, data: { as_of, golden_pit_window: {...}, indices: [...], triple_confirmation: {...}, prediction: {...}, summary, global_macro: {...} } }`

**要点**：
- `indices` 为全量逐指数列表，只关注 `status` 为 `golden_pit` / `warning` 的指数即可
- `summary` 为可直接引用的总结文本；`golden_pit_window` 给出窗口阶段（active/waiting/idle）、当前天与坑内/拐点指数数

---

## 18. get_golden_pit_history — 贪婪值历史走势

**用途**：获取宽基指数贪婪值历史序列（DB 快照，日频），用于复盘贪婪值趋势、极值与分位判断。

**数据源**：本地 DB（golden_pit_snapshot）

**请求**：`GET /golden-pit/history?index={fund_code}&days={n}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| index | string | 否 | 基金代码，如 `510300` / `588000` / `159845`；默认 `all` 返回全部 |
| days | number | 否 | 返回天数，1-2000，默认 60 |

**返回**：`{ code: 0, data: { as_of, series: { [fund_code]: [{ date, greed, close }] }, indices: { [fund_code]: name } } }`

---

## 19. get_golden_pit_dca_status — 黄金坑DCA状态

**用途**：查看黄金坑 DCA 自动定投当前状态：窗口是否活跃、每只 ETF 的执行进度（已投/待投天数、累计金额、剩余额度、趋势因子）。

**数据源**：状态接口（内部调用 get_status）+ DB（golden_pit_etf_config / golden_pit_dca_log）

**请求**：`GET /golden-pit/dca/status`

**参数**：无

**返回**：`{ code: 0, data: { as_of, window_active, window_phase, current_day, window_start, pit_count, turning_count, resonance_multiplier, global_macro, etfs: [{ fund_code, index_name, etf_code, status, strategy, dca_strategy, daily_amount, max_total_amount, total_invested, remaining, executed_days, pending_days, planned_days, trend, trend_factor }] } }`

---

## 20. get_golden_pit_dca_logs — 黄金坑DCA日志

**用途**：查看 DCA 定投执行历史记录（时间、指数、ETF、窗口天、金额、策略、成交状态），可筛选。

**数据源**：DB（golden_pit_dca_log）

**请求**：`GET /golden-pit/dca/logs?days={n}&fund_code={code}`

**参数**：
| 参数 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| days | number | 否 | 查询最近 N 天，默认 30，最大 365 |
| fund_code | string | 否 | 筛选基金代码，空则全部 |

**返回**：`{ code: 0, data: [{ id, fund_code, window_start, buy_day, etf_code, amount, strategy, order_id, status, created_at }] }`

---

## 21. get_golden_pit_etf_configs — 黄金坑ETF配置

**用途**：查看所有黄金坑 ETF 定投配置（策略类型、日投金额、总上限、触发条件、启用状态）。

**数据源**：DB（golden_pit_etf_config）

**请求**：`GET /golden-pit/etf-configs`

**参数**：无

**返回**：`{ code: 0, data: [{ id, fund_code, index_name, etf_code, etf_name, priority, strategy, daily_amount, max_total_amount, max_position_pct, require_absolute_threshold, min_days_in_pit, skip_if_already_holding, enabled, notes }] }`

**要点**：`strategy` 常见取值 `uniform_3/5/7/10/15`、`front_loaded`、`triangle`、`lump_entry`；`require_absolute_threshold` 为 true 表示需绝对阈值 0.35 触发，否则为 P10 相对触发。

---

## 使用要点

### 黄金坑话题

当讨论涉及黄金坑信号、贪婪值、DCA 定投进度或黄金坑 ETF 配置时：
1. **首先调用 get_golden_pit_status** — 判断当前是否处于黄金坑区域、哪些指数在坑/预警
2. 用 get_golden_pit_history 补充贪婪值历史趋势（可传基金代码或 all）
3. get_golden_pit_dca_status / get_golden_pit_dca_logs / get_golden_pit_etf_configs 用于复盘定投执行进度、历史成交与配置
4. 严禁在未调用工具的情况下凭空编造黄金坑信号或 DCA 执行数据

### 数据时效性区分（重要）

| 工具 | 数据类型 | 可靠性 | 使用场景 |
|------|----------|:------:|----------|
| get_realtime_indicators | 盘中实时估算 | ⭐⭐ | 辅助参考，不能作为独立建仓理由 |
| get_technical | 盘后日频确认 | ⭐⭐⭐ | 趋势判断和金叉死叉确认 |
| get_daily_kline_qfq | 日K线原始数据 | ⭐⭐⭐ | 趋势分析、支撑阻力位 |

**规则**：
- `get_technical` 返回的是最后一个收盘日的已确认值，**不是当日盘中值**
- `get_realtime_indicators` 的 KDJ/MACD/RSI 标记为 `intraday_estimate`（盘中估算），今日高/低点未最终确认
- 建仓决策以 `get_technical` 的盘后确认信号为主，`get_realtime_indicators` 为辅
- 严禁在未调用上述工具的情况下凭空编造技术指标信号

### 工具使用优先级

当用户询问某只股票的买卖建议时：
1. **首先调用 get_trade_advice** — 获取完整操作信号（买入/持有/卖出/观望）
2. 然后用 get_daily_kline_qfq、get_moneyflow、get_technical 交叉验证
3. get_fibonacci_levels 和 get_daily_channel 用于单独查看斐波那契价位或日内通道，不需要重复获取操作建议

### 建仓前必调工具

- **check_entry_filters** — 三层过滤（技术面/主力行为/超买）
- **calc_position** — 仓位计算（建议股数/止损价/铁律二/风险验证）

### 概念映射查询

查询股票所属概念板块，使用 `stock_pool.db` 的 `stock_concept_map` 表（通过 `read_db_table`）：
- `ts_code` 格式：`代码.交易所`（如 `000001.SZ`）
- `symbol`：纯数字代码（如 `000001`）

---

## 调用示例

### 示例 1：分析某只股票

```http
# 1. 获取操作建议
POST http://81.70.44.68/api/v1/indicator/advice
Content-Type: application/json

{ "symbol": "SH600519", "cost": 1700.0 }

# 2. 获取前复权日K线
GET http://81.70.44.68/api/v1/market/pro-bar/SH600519?adj=qfq&limit=30

# 3. 获取盘后技术指标
GET http://81.70.44.68/api/v1/market/technical/SH600519?limit=30

# 4. 获取资金流向
GET http://81.70.44.68/api/v1/market/moneyflow/SH600519
```

### 示例 2：复盘整周策略

```http
# 1. 获取整周 Pi 分析历史
GET http://81.70.44.68/api/v1/scan/pi-analysis?start_date=2024-05-20&end_date=2024-05-24

# 2. 获取整周交易报告历史
GET http://81.70.44.68/api/v1/scan/trade-reports?start_date=2024-05-20&end_date=2024-05-24

# 3. 查询持仓股票的前复权K线和技术指标
GET http://81.70.44.68/api/v1/market/pro-bar/SH600519?adj=qfq&limit=30
GET http://81.70.44.68/api/v1/market/technical/SH600519?limit=30
```

### 示例 3：建仓前检查

```http
# 1. 入场三层过滤
POST http://81.70.44.68/api/v1/indicator/check-entry-filters
Content-Type: application/json

{ "symbol": "SH600519", "sector_net_inflow": 50000000, "volume_ratio": 1.8 }

# 2. 仓位计算
POST http://81.70.44.68/api/v1/indicator/calc-position
Content-Type: application/json

{ "symbol": "SH600519", "signal_strength": "high", "chain_role": "upstream", "tier": "probe", "stance": "green" }
```

---

## 数据源说明

| 数据源 | 工具 | 时效 |
|--------|------|------|
| Tushare pro_bar | get_daily_kline_qfq | 日频·盘后 |
| Tushare stk_factor_pro | get_technical | 日频·盘后确认 |
| 腾讯 qt.gtimg.cn + Tushare | get_realtime_indicators | 实时·盘中估算 |
| 东方财富 / 同花顺 | get_moneyflow | 实时 |
| Tushare fina_mainbz | get_fina_mainbz | 静态·定期报告 |
| Tushare express | get_express | 静态·定期报告 |
| 后端扫描/Pi 分析记录 | get_pi_analysis_history, get_trade_history, get_latest_scan_report | 历史记录 |
| 本地 SQLite | read_db_table, get_db_schema | 静态 |
| ArkVol + 本地快照 DB | get_golden_pit_status, get_golden_pit_history | 日频·快照 |
| DB（golden_pit_dca_log / golden_pit_etf_config） | get_golden_pit_dca_status, get_golden_pit_dca_logs, get_golden_pit_etf_configs | 日频·实时查询 |

---

## 限制

- **无交易执行权限**：本工具集不含 `place_order` / `get_orders` / `cancel_order`
- **只读**：所有工具均为查询操作，不会修改任何数据
- **黄金坑只读**：黄金坑工具仅含查询，不含 `update_golden_pit_etf_config`（定投配置修改）与 DCA 手动执行
