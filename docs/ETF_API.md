# Marcus ETF API 接口文档

**版本**: v2.0
**更新时间**: 2026-05-18
**基础路径**: `/api/v1/etf`

---

## 概述

ETF API 提供 ETF 板块池管理、实时行情查询、K线数据、候选选股等功能，支持 Marcus 短线右侧交易系统。

**数据来源**: 雪球 API (`stock.xueqiu.com`)

**认证方式**: 需要在 `core/config.json` 中配置 `token` 和 `u` cookie

---

## 认证说明

接口调用需要携带雪球认证 Cookie，在 `core/config.json` 中配置：

```json
{
  "token": "xq_a_token=<your_token>;",
  "u": "<your_user_id>"
}
```

| Cookie | 必填 | 说明 |
|--------|------|------|
| `xq_a_token` | ✅ | 雪球认证令牌 |
| `u` | ✅ | 用户ID（任意值都可） |

---

## 接口列表

| 方法 | 路径 | 描述 |
|------|------|------|
| POST | `/api/v1/etf/sync` | 同步ETF板块池到数据库 |
| GET | `/api/v1/etf/list` | 获取ETF板块池列表（从数据库） |
| GET | `/api/v1/etf/quotes` | 获取ETF实时行情（批量） |
| GET | `/api/v1/etf/candidates` | 获取ETF候选列表（三重确认选股） |
| GET | `/api/v1/etf/sectors/hot` | 获取热点板块排名 |
| GET | `/api/v1/etf/quote/{symbol}` | 获取单只ETF实时行情 |
| GET | `/api/v1/etf/detail/{symbol}` | 获取ETF详细信息 |
| GET | `/api/v1/etf/kline/{symbol}` | 获取ETF K线数据 |

---

## 1. 同步ETF板块池

**路径**: `POST /api/v1/etf/sync`

**描述**: 从雪球API同步ETF板块池数据到本地数据库，支持分页获取。同步完成后可通过 `/etf/list` 查询。

**请求参数**:

| 参数名 | 类型 | 必填 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `pages` | int | 否 | 5 | 同步前几页（每页30只，约1520只ETF） |

**请求示例**:

```
POST /api/v1/etf/sync
POST /api/v1/etf/sync?pages=10
```

**响应示例**:

```json
{
  "synced": 150,
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `synced` | int | 同步的ETF数量 |
| `updated_at` | string | 同步完成时间 |

---

## 2. 获取ETF板块池列表

**路径**: `GET /api/v1/etf/list`

**描述**: 从本地数据库获取ETF板块池列表，支持板块筛选。

**请求参数**:

| 参数名 | 类型 | 必填 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `sector` | string | 否 | - | 板块名称筛选（如"半导体"） |
| `limit` | int | 否 | 100 | 返回数量限制 |

**请求示例**:

```
GET /api/v1/etf/list
GET /api/v1/etf/list?limit=20
GET /api/v1/etf/list?sector=半导体
```

**响应示例**:

```json
{
  "etf_list": [
    {
      "symbol": "SZ159813",
      "name": "半导体ETF鹏华",
      "sector": "",
      "catalyst_type": "",
      "priority": 3,
      "data": {
        "symbol": "SZ159813",
        "name": "半导体ETF鹏华",
        "percent": 4.06,
        "current": 1.539,
        "amount": 225449872.0,
        "volume": 149201839,
        "market_capital": 5495557248.99,
        "premium_rate": 0.13,
        "unit_nav": 1.482,
        "current_year_percent": 38.77
      }
    },
    {
      "symbol": "SZ159995",
      "name": "芯片ETF华夏",
      "sector": "",
      "catalyst_type": "",
      "priority": 3,
      "data": {
        "symbol": "SZ159995",
        "name": "芯片ETF华夏",
        "percent": 2.85,
        "current": 1.234,
        "amount": 156000000.0
      }
    }
  ],
  "sector_count": 45,
  "total_count": 1397,
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `etf_list` | array | ETF列表 |
| `etf_list[].symbol` | string | ETF代码 |
| `etf_list[].name` | string | ETF名称 |
| `etf_list[].sector` | string | 所属板块（暂未分类填空） |
| `etf_list[].catalyst_type` | string | 催化剂类型（暂未分类填空） |
| `etf_list[].priority` | int | 优先级（默认3） |
| `etf_list[].data` | object | 完整行情数据 |
| `sector_count` | int | 涉及板块数量 |
| `total_count` | int | ETF总数 |
| `updated_at` | string | 更新时间 |

---

## 3. 获取ETF实时行情（批量）

**路径**: `GET /api/v1/etf/quotes`

**描述**: 批量获取ETF实时行情数据，包括现价、涨跌幅、成交额、估算换手率等。

**请求参数**:

| 参数名 | 类型 | 必填 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `symbols` | string | 否 | - | ETF代码列表，逗号分隔。如不指定则返回前 `top_n` 只 |
| `top_n` | int | 否 | 12 | 当未指定 symbols 时，返回前 N 只 ETF 的行情 |

**请求示例**:

```
GET /api/v1/etf/quotes
GET /api/v1/etf/quotes?top_n=6
GET /api/v1/etf/quotes?symbols=SZ159813,SZ159995,SH512760
```

**响应示例**:

```json
{
  "quotes": [
    {
      "symbol": "SZ159813",
      "name": "半导体ETF鹏华",
      "sector": "",
      "current": 1.539,
      "percent": 4.06,
      "amount": 225449872.0,
      "volume": 149201839,
      "turnover_rate_est": 4.1,
      "last_close": 1.492,
      "high": 1.563,
      "low": 1.515
    }
  ],
  "count": 1,
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `quotes` | array | 行情列表 |
| `quotes[].symbol` | string | ETF代码 |
| `quotes[].name` | string | ETF名称 |
| `quotes[].sector` | string | 所属板块 |
| `quotes[].current` | float | 当前价格 |
| `quotes[].percent` | float | 涨跌幅（%） |
| `quotes[].amount` | float | 成交额（元） |
| `quotes[].volume` | float | 成交量（股） |
| `quotes[].turnover_rate_est` | float | 估算换手率（%） |
| `quotes[].last_close` | float | 昨日收盘价 |
| `quotes[].high` | float | 今日最高价 |
| `quotes[].low` | float | 今日最低价 |
| `count` | int | 返回行情数量 |
| `updated_at` | string | 数据更新时间 |

---

## 4. 获取ETF候选列表

**路径**: `GET /api/v1/etf/candidates`

**描述**: 基于三重确认（板块催化 + Momentum + 流动性）返回候选 ETF 列表，用于交易决策。

**ETF 建仓三重确认**:
1. ✅ **板块催化** — 板块新闻分 ≥ 55（必要条件）
2. ✅ **Momentum 确认** — pct_1d ≥ 1.5%（技术过滤，市场立场影响门槛）
3. ✅ **流动性确认** — 成交额 ≥ 1亿元

**请求参数**:

| 参数名 | 类型 | 必填 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `top_n` | int | 否 | 5 | 返回最多多少只候选 |
| `market_stance` | string | 否 | yellow | 市场立场：green/yellow/red |
| `hot_sectors` | string | 否 | - | 外部热点行业，逗号分隔 |

**Momentum 门槛（根据市场立场）**:

| 市场立场 | Momentum 门槛 | 说明 |
|---------|---------------|------|
| green（强势） | -1.0% | 允许补涨 |
| yellow（中性） | 0.5% | 轻度要求 momentum |
| red（弱势） | 1.5% | 提高要求，防御优先 |

**请求示例**:

```
GET /api/v1/etf/candidates
GET /api/v1/etf/candidates?top_n=5&market_stance=yellow
GET /api/v1/etf/candidates?top_n=3&market_stance=green&hot_sectors=半导体,AI
```

**响应示例**:

```json
{
  "candidates": [
    {
      "symbol": "SZ159813",
      "name": "半导体ETF鹏华",
      "sector": "",
      "catalyst_score": 72.5,
      "catalyst_tag": "🟢",
      "pct_1d": 4.06,
      "momentum_score": 48.3,
      "sector_news_score": 50.0,
      "amount": 225449872.0
    }
  ],
  "count": 1,
  "market_stance": "yellow",
  "hot_sectors": [],
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `candidates` | array | 候选ETF列表（按评分降序） |
| `candidates[].symbol` | string | ETF代码 |
| `candidates[].name` | string | ETF名称 |
| `candidates[].sector` | string | 所属板块 |
| `candidates[].catalyst_score` | float | 综合催化评分（满分100） |
| `candidates[].catalyst_tag` | string | 标签：🟢(≥70) 🟡(55-70) 🔴(<55) |
| `candidates[].pct_1d` | float | 今日涨跌幅（%） |
| `candidates[].momentum_score` | float | Momentum评分（0-50） |
| `candidates[].sector_news_score` | float | 板块催化评分（0-50） |
| `candidates[].amount` | float | 成交额（元） |
| `market_stance` | string | 当前市场立场 |
| `hot_sectors` | array | 外部热点行业列表 |

**catalyst_score 计算公式**:

```
catalyst_score = sector_news_score * 0.6 + momentum_score * 0.4
- sector_news_score: 板块催化评分（0-50），优先级高板块得分更高
- momentum_score: Momentum评分（0-50），基于 pct_1d 计算
```

---

## 5. 获取热点板块

**路径**: `GET /api/v1/etf/sectors/hot`

**描述**: 返回按 ETF 配置优先级排序的热点板块列表。

**请求参数**:

| 参数名 | 类型 | 必填 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `limit` | int | 否 | 5 | 返回前 N 个热点板块 |

**请求示例**:

```
GET /api/v1/etf/sectors/hot
GET /api/v1/etf/sectors/hot?limit=8
```

**响应示例**:

```json
{
  "hot_sectors": [
    {"sector": "半导体", "priority": 1, "etf_count": 45, "news_score": 45.0},
    {"sector": "军工", "priority": 1, "etf_count": 28, "news_score": 40.0},
    {"sector": "新能源", "priority": 2, "etf_count": 32, "news_score": 35.0},
    {"sector": "消费", "priority": 2, "etf_count": 25, "news_score": 30.0}
  ],
  "count": 4,
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `hot_sectors` | array | 热点板块列表 |
| `hot_sectors[].sector` | string | 板块名称 |
| `hot_sectors[].priority` | int | 优先级（1=最高） |
| `hot_sectors[].etf_count` | int | 该板块内ETF数量 |
| `hot_sectors[].news_score` | float | 新闻情绪评分（估算） |
| `count` | int | 返回板块数量 |

---

## 6. 获取单只ETF行情

**路径**: `GET /api/v1/etf/quote/{symbol}`

**描述**: 获取指定ETF代码的实时行情。

**路径参数**:

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| `symbol` | string | 是 | ETF代码（如 SH512480、SZ159813） |

**请求示例**:

```
GET /api/v1/etf/quote/SZ159813
GET /api/v1/etf/quote/SZ159995
```

**响应示例**:

```json
{
  "symbol": "SZ159813",
  "name": "半导体ETF鹏华",
  "current": 1.539,
  "percent": 4.06,
  "amount": 225449872.0,
  "volume": 149201839,
  "turnover_rate_est": 4.1,
  "last_close": 1.492,
  "high": 1.563,
  "low": 1.515,
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `symbol` | string | ETF代码 |
| `name` | string | ETF名称 |
| `current` | float | 当前价格 |
| `percent` | float | 涨跌幅（%） |
| `amount` | float | 成交额（元） |
| `volume` | float | 成交量（股） |
| `turnover_rate_est` | float | 估算换手率（%） |
| `last_close` | float | 昨日收盘价 |
| `high` | float | 今日最高价 |
| `low` | float | 今日最低价 |
| `updated_at` | string | 数据更新时间 |

**错误响应** (404):

```json
{
  "detail": "ETF SH512480 not found"
}
```

---

## 7. 获取ETF详细信息

**路径**: `GET /api/v1/etf/detail/{symbol}`

**描述**: 获取ETF的详细信息，包括净值、溢价率、规模等。

**路径参数**:

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| `symbol` | string | 是 | ETF代码 |

**请求示例**:

```
GET /api/v1/etf/detail/SZ159530
```

**响应示例**:

```json
{
  "status": "交易中",
  "status_id": 5,
  "region": "CN",
  "time_zone": "Asia/Shanghai",
  "symbol": "SZ159530",
  "code": "159530",
  "name": "机器人ETF易方达",
  "current": 1.662,
  "percent": 0.67,
  "chg": 0.011,
  "last_close": 1.651,
  "open": 1.645,
  "high": 1.663,
  "low": 1.632,
  "volume": 132038105,
  "amount": 217288395.0,
  "market_capital": 15200009086.88,
  "unit_nav": 1.646,
  "acc_unit_nav": 1.646,
  "iopv": 1.66,
  "premium_rate": 0.12,
  "found_date": 1704816000000,
  "issue_date": 1705507200000,
  "nav_date": 1778774400000,
  "pankou_ratio": 44.38,
  "high52w": 1.765,
  "low52w": 0.796,
  "current_year_percent": 4.79,
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `symbol` | string | ETF代码 |
| `name` | string | ETF名称 |
| `current` | float | 当前价格 |
| `percent` | float | 涨跌幅（%） |
| `chg` | float | 涨跌额 |
| `last_close` | float | 昨日收盘价 |
| `open` | float | 今日开盘价 |
| `high` | float | 今日最高价 |
| `low` | float | 今日最低价 |
| `volume` | float | 成交量（股） |
| `amount` | float | 成交额（元） |
| `market_capital` | float | 规模（元） |
| `unit_nav` | float | 单位净值 |
| `acc_unit_nav` | float | 累计净值 |
| `iopv` | float | 实时估值（IOPV） |
| `premium_rate` | float | 溢价率（%） |
| `found_date` | int | 成立日期（时间戳，毫秒） |
| `issue_date` | int | 上市日期（时间戳，毫秒） |
| `nav_date` | int | 净值日期（时间戳，毫秒） |
| `pankou_ratio` | float | 盘口比 |
| `high52w` | float | 52周最高价 |
| `low52w` | float | 52周最低价 |
| `current_year_percent` | float | 今年涨跌幅（%） |
| `status` | string | 交易状态（交易中/未开盘/已休市） |

---

## 8. 获取ETF K线数据

**路径**: `GET /api/v1/etf/kline/{symbol}`

**描述**: 获取ETF的历史K线数据，支持多种周期。

**路径参数**:

| 参数名 | 类型 | 必填 | 描述 |
|--------|------|------|------|
| `symbol` | string | 是 | ETF代码 |

**查询参数**:

| 参数名 | 类型 | 必填 | 默认值 | 描述 |
|--------|------|------|--------|------|
| `period` | string | 否 | day | K线周期：day/week/month/minute/5minute/15minute/30minute/60minute |
| `count` | int | 否 | -284 | 数据条数，负数表示取起点之前的历史数据 |
| `begin` | int | 否 | 当前时间 | 起始时间戳（毫秒） |

**请求示例**:

```
GET /api/v1/etf/kline/SZ159530
GET /api/v1/etf/kline/SZ159530?period=day&count=-100
GET /api/v1/etf/kline/SZ159530?period=week&count=-52
```

**响应示例**:

```json
{
  "symbol": "SZ159530",
  "period": "day",
  "count": -10,
  "klines": [
    {
      "timestamp": 1777392000000,
      "volume": 230598900,
      "open": 1.438,
      "high": 1.472,
      "low": 1.432,
      "close": 1.467,
      "chg": 0.021,
      "percent": 1.45,
      "turnoverrate": 0.0,
      "amount": 336463034.0,
      "volume_post": null,
      "amount_post": null
    },
    {
      "timestamp": 1777478400000,
      "volume": 302822531,
      "open": 1.465,
      "high": 1.497,
      "low": 1.465,
      "close": 1.49,
      "chg": 0.029,
      "percent": 1.98,
      "turnoverrate": 0.0,
      "amount": 451006133.0,
      "volume_post": null,
      "amount_post": null
    }
  ],
  "count": 10,
  "updated_at": "2026-05-18T10:30:00"
}
```

**响应字段说明**:

| 字段 | 类型 | 描述 |
|------|------|------|
| `symbol` | string | ETF代码 |
| `period` | string | K线周期 |
| `count` | int | 请求条数 |
| `klines` | array | K线数据列表 |
| `klines[].timestamp` | int | 时间戳（毫秒） |
| `klines[].open` | float | 开盘价 |
| `klines[].high` | float | 最高价 |
| `klines[].low` | float | 最低价 |
| `klines[].close` | float | 收盘价 |
| `klines[].volume` | float | 成交量（股） |
| `klines[].amount` | float | 成交额（元） |
| `klines[].chg` | float | 涨跌额 |
| `klines[].percent` | float | 涨跌幅（%） |
| `klines[].turnoverrate` | float | 换手率 |
| `count` | int | 返回K线条数 |

---

## 错误码说明

| HTTP 状态码 | 描述 |
|-------------|------|
| 200 | 请求成功 |
| 400 | 参数错误 |
| 404 | ETF代码不存在 |
| 500 | 服务器内部错误 |

---

## 使用示例

### cURL

```bash
# 同步ETF板块池到数据库
curl -X POST "http://localhost:8000/api/v1/etf/sync?pages=5"

# 获取ETF列表
curl "http://localhost:8000/api/v1/etf/list"

# 获取前6只ETF行情
curl "http://localhost:8000/api/v1/etf/quotes?top_n=6"

# 获取候选ETF（中性市场）
curl "http://localhost:8000/api/v1/etf/candidates?top_n=5&market_stance=yellow"

# 获取单只ETF详情
curl "http://localhost:8000/api/v1/etf/detail/SZ159530"

# 获取ETF K线（日线，近100天）
curl "http://localhost:8000/api/v1/etf/kline/SZ159530?period=day&count=-100"
```

### Python

```python
import requests

base_url = "http://localhost:8000/api/v1"

# 同步ETF板块池
response = requests.post(f"{base_url}/etf/sync", params={"pages": 5})
print(f"同步了 {response.json()['synced']} 只ETF")

# 获取ETF列表
response = requests.get(f"{base_url}/etf/list", params={"limit": 20})
for etf in response.json()["etf_list"]:
    print(f"{etf['symbol']}: {etf['name']}")

# 获取ETF行情
response = requests.get(f"{base_url}/etf/quotes", params={"top_n": 6})
for q in response.json()["quotes"]:
    print(f"{q['symbol']} {q['name']}: {q['percent']:+.2f}%")

# 获取候选ETF
response = requests.get(
    f"{base_url}/etf/candidates",
    params={"top_n": 5, "market_stance": "yellow"}
)
for c in response.json()["candidates"]:
    print(f"{c['catalyst_tag']} {c['symbol']}: 评分={c['catalyst_score']}")

# 获取ETF K线
response = requests.get(
    f"{base_url}/etf/kline/SZ159530",
    params={"period": "day", "count": -100}
)
klines = response.json()["klines"]
print(f"获取到 {len(klines)} 条K线数据")
```

### JavaScript (Node.js)

```javascript
const axios = require('axios');
const baseUrl = 'http://localhost:8000/api/v1';

async function main() {
  // 同步ETF板块池
  let res = await axios.post(`${baseUrl}/etf/sync`, null, {params: {pages: 5}});
  console.log(`同步了 ${res.data.synced} 只ETF`);

  // 获取ETF列表
  res = await axios.get(`${baseUrl}/etf/list`, {params: {limit: 20}});
  console.log(`共 ${res.data.total_count} 只ETF`);

  // 获取行情
  res = await axios.get(`${baseUrl}/etf/quotes`, {params: {top_n: 6}});
  res.data.quotes.forEach(q => {
    console.log(`${q.symbol} ${q.name}: ${q.percent > 0 ? '+' : ''}${q.percent}%`);
  });
}

main();
```

---

## 数据源API

雪球ETF数据来源：

| 雪球API | 用途 |
|---------|------|
| `GET /v5/stock/screener/fund/list.json` | ETF列表/筛选 |
| `GET /v5/stock/quote.json?extend=detail` | ETF详细信息 |
| `GET /v5/stock/chart/kline.json` | ETF历史K线 |

雪球认证所需Cookie：
- `xq_a_token`: 认证令牌
- `u`: 用户ID

---

## 相关文档

- [雪球API接口](../core/xueqiu_engine.py)

---

_保持饥饿，保持锋利。🐆_