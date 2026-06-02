# Marcus AI Trading Platform - 项目文档

## 1. 项目概述

Marcus 是一个基于 AI 的 A 股模拟交易平台，支持实时行情、新闻情绪分析、策略管理和任务调度。

### 核心功能
- **账户管理**: 持仓查询、资金统计、FIFO成本计算
- **交易执行**: 买入/卖出交易、订单管理、交易历史
- **行情数据**: A股指数、美股指数、大宗商品、板块行情
- **新闻资讯**: 财经新闻采集、DeepSeek AI情绪分析
- **策略管理**: 市场扫描、仓位态度、动态watchlist
- **任务调度**: Cron任务调度、执行历史、通知机制
- **AI助手**: 基于Function Calling的聊天机器人

### 技术架构
```
marcus-platform/
├── backend/              # FastAPI 后端 (Python 3.12)
│   ├── app/
│   │   ├── api/          # REST API 端点
│   │   ├── models/       # Pydantic 数据模型
│   │   ├── services/     # 业务服务
│   │   ├── core/trading/ # 交易引擎
│   │   └── agent/        # AI Agent
│   ├── requirements.txt
├── frontend/             # React + Vite + TypeScript
│   ├── src/
│   │   ├── pages/        # 页面组件
│   │   ├── components/   # 布局组件
│   │   ├── api/          # API客户端
│   │   └── i18n/         # 国际化
│   └── package.json
├── config/               # 配置文件
│   └── tasks.yaml        # 调度任务配置
├── docker/               # Docker 配置
└── .env.example          # 环境变量模板
```

---

## 2. 环境依赖

### 后端依赖 (backend/requirements.txt)
```
fastapi>=0.100.0
uvicorn[standard]>=0.23.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
httpx>=0.24.0
apscheduler>=3.10.0
pyyaml>=6.0
```

### 前端依赖 (frontend/package.json)
```json
{
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0",
    "react-i18next": "^14.0.0",
    "i18next": "^23.7.0",
    "axios": "^1.6.0",
    "recharts": "^2.10.0",
    "lucide-react": "^0.294.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.2.0",
    "vite": "^5.0.0",
    "typescript": "^5.3.0",
    "tailwindcss": "^3.3.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0"
  }
}
```

### 外部数据源 (必须配置)
| 数据源 | 用途 | 获取方式 |
|--------|------|----------|
| DeepSeek API | AI对话、情绪分析 | siliconflow.cn |
| Tushare Token | A股数据 | tushare.pro |
| Xueqiu Token | 雪球实时行情 | xueqiu.com |

### 系统依赖
- Python 3.12+
- Node.js 18+
- PostgreSQL 15 (可选，默认使用SQLite)
- Redis 7 (可选)

---

## 3. 配置说明

### 环境变量 (.env)
```bash
# 数据库
DATABASE_URL=postgresql://marcus:password@localhost:5432/marcus_trading
REDIS_URL=redis://localhost:6379/0

# API密钥 (必须配置)
DEEPSEEK_API_KEY=your_api_key
DEEPSEEK_API_HOST=api.siliconflow.cn
DEEPSEEK_MODEL=deepseek-ai/DeepSeek-V4-Flash

TUSHARE_TOKEN=your_tushare_token
XUEQIU_TOKEN=xq_a_token=your_xueqiu_token

# 服务器
API_HOST=0.0.0.0
API_PORT=8000
```

### 调度任务配置 (config/tasks.yaml)
```yaml
settings:
  workspace: F:/path/to/workspace-marcus
  job_timeout: 900

tasks:
  - id: market-scan
    name: 市场扫描
    description: 每日市场扫描并更新策略
    enabled: true
    schedule:
      type: cron
      expr: "0 8,12,15 * * 1-5"
      timezone: Asia/Shanghai
    script:
      path: scripts/market_scan.py
      args: []
```

---

## 4. 目录结构规范

### 核心目录 (可移植组件)

#### backend/app/api/
REST API 端点模块。每个功能模块一个文件：

| 文件 | 端点前缀 | 功能 |
|------|----------|------|
| portfolio.py | /api/v1/portfolio | 账户总览、持仓查询 |
| trades.py | /api/v1/trades | 交易执行、历史查询 |
| market.py | /api/v1/market | 行情数据 (指数、板块、个股) |
| news.py | /api/v1/news | 新闻流、情绪分析 |
| strategy.py | /api/v1/strategy | 策略状态、扫描历史 |
| scheduler.py | /api/v1/scheduler | 任务调度管理 |
| agent.py | /api/v1/agent | AI聊天助手 |

#### backend/app/models/
Pydantic 数据模型，定义API请求/响应数据结构：

| 文件 | 模型 | 用途 |
|------|------|------|
| account.py | AccountResponse, PositionResponse | 账户、持仓响应 |
| trade.py | TradeRequest, TradeResponse, OrderResponse | 交易请求/响应 |
| market.py | IndexResponse, QuoteResponse, SectorResponse | 行情数据模型 |
| news.py | NewsResponse, SentimentResponse | 新闻、情绪模型 |
| strategy.py | StrategyResponse, ScanResponse | 策略模型 |

#### backend/app/services/
业务服务层：

| 文件 | 功能 |
|------|------|
| scheduler_service.py | APScheduler任务调度服务 |

#### backend/app/core/trading/
交易执行引擎：

| 文件 | 功能 |
|------|------|
| marcus_trade.py | MarcusVNPyExecutor 交易执行器 |
| paper_engine.py | PaperTradingEngine 模拟交易引擎 |

#### backend/app/agent/
AI Agent 模块：

| 文件 | 功能 |
|------|------|
| session.py | 会话管理 |
| storage.py | 持久化存储 |
| providers/base.py | LLM Provider抽象 |
| skills_loader.py | Skills加载器 |
| compactor.py | 上下文压缩 |

#### frontend/src/
React前端应用：

| 目录 | 功能 |
|------|------|
| pages/ | 页面组件 (PortfolioPage, TradingPage, MarketPage...) |
| components/ | 布局组件 (Layout, LanguageSwitcher) |
| api/client.ts | Axios API客户端 |
| i18n/ | 国际化 (en.json, zh.json) |

---

## 5. API 接口规范

### 基础信息
- Base URL: `http://localhost:8000/api/v1`
- 认证方式: 无 (内部使用)
- 数据格式: JSON

### 端点列表

#### 健康检查
```
GET /health
Response: { "status": "healthy", "timestamp": "...", "scheduler": {...} }
```

#### 账户 & 持仓
```
GET /portfolio
Response: { account: AccountResponse, total_return, total_return_pct, win_rate }

GET /portfolio/positions
Response: PositionResponse[]
```

#### 交易
```
POST /trades
Body: { "symbol": "SH600519", "side": "buy", "price": 1700.0, "volume": 100, "reason": "..." }
Response: TradeResponse

GET /trades
Query: symbol?, limit, page
Response: { trades: OrderResponse[], total, page, page_size }

GET /trades/{order_id}
Response: OrderResponse
```

#### 行情
```
GET /market/indices
Response: { indices: IndexResponse[], updated_at }

GET /market/quote/{symbol}
Response: QuoteResponse

GET /market/sectors
Response: { sectors: SectorResponse[], sentiment, updated_at }

GET /market/global
Response: { us_indices, commodities, updated_at }
```

#### 新闻 & 情绪
```
GET /news
Query: symbol?, limit, page
Response: { news: NewsResponse[], total, page, page_size }

GET /news/sentiment
Response: SentimentResponse
```

#### 策略
```
GET /strategy/current
Response: StrategyResponse

GET /strategy/scans
Query: limit
Response: { scans: ScanResponse[], total }
```

#### 调度器
```
GET /scheduler/status
GET /scheduler/tasks
GET /scheduler/tasks/{task_id}
GET /scheduler/tasks/{task_id}/executions
POST /scheduler/tasks/{task_id}/trigger
POST /scheduler/tasks/{task_id}/enable
POST /scheduler/tasks/{task_id}/disable
PATCH /scheduler/tasks/{task_id}
GET /scheduler/next-runs
POST /scheduler/reload
```

#### AI Agent
```
POST /agent/chat
Body: { "message": "...", "session_id": "...", "context": [...] }
Response: { "response": "...", "tool_calls": [...], "session_id": "...", "timestamp": "..." }

GET /agent/tools
GET /agent/skills
```

---

## 6. 数据模型

### AccountResponse
```typescript
{
  initial_capital: number      // 初始资金
  available_cash: number       // 可用资金
  frozen_cash: number          // 冻结资金
  position_value: number       // 持仓市值
  total_asset: number          // 总资产
  realized_pnl: number         // 已实现盈亏
  float_pnl: number            // 浮动盈亏
  total_pnl: number            // 总盈亏
  position_ratio: number       // 持仓比例 %
  positions: Position[]        // 持仓列表
  updated_at: datetime
}
```

### PositionResponse
```typescript
{
  symbol: string               // 股票代码
  name: string                // 股票名称
  volume: number              // 持仓数量
  avg_price: number           // 成本价
  current_price: number       // 当前价
  market_value: number        // 市值
  floating_pnl: number         // 浮动盈亏
  floating_pnl_pct: number     // 盈亏比例 %
  entry_date: string
}
```

### TradeRequest
```typescript
{
  symbol: string              // 股票代码 (SH600519)
  side: "buy" | "sell"        // 买卖方向
  price: number               // 价格
  volume: number              // 数量 (100的整数倍)
  reason?: string             // 交易原因
}
```

### IndexResponse
```typescript
{
  symbol: string              // 指数代码
  name: string               // 指数名称
  current_price: number
  last_close: number
  change: number              // 涨跌额
  change_pct: number          // 涨跌幅 %
  volume: number
  high: number
  low: number
  open_price: number
  gap_pct: number             // 跳空缺口 %
  updated_at: datetime
}
```

---

## 7. 前端组件规范

### 页面组件 (frontend/src/pages/)
每个页面对应一个路由：

| 组件 | 路由 | 功能 |
|------|------|------|
| PortfolioPage | /portfolio | 账户总览、持仓管理 |
| TradingPage | /trading | 新建交易、订单历史 |
| MarketPage | /market | 指数行情、板块热点 |
| NewsPage | /news | 新闻流、情绪分析 |
| StrategyPage | /strategy | 当前策略、watchlist |
| AnalyticsPage | /analytics | 收益图表、统计分析 |
| SchedulerPage | /scheduler | 任务调度、执行历史 |
| TradingAgentPage | /agent | AI聊天界面 |

### API客户端 (frontend/src/api/client.ts)
```typescript
// 每个API模块对应的调用方法
export const portfolioApi = {
  getSummary: () => api.get('/portfolio'),
  getPositions: () => api.get('/portfolio/positions'),
}

export const tradesApi = {
  execute: (data) => api.post('/trades', data),
  getHistory: (params) => api.get('/trades', { params }),
}

export const marketApi = {
  getIndices: () => api.get('/market/indices'),
  getQuote: (symbol) => api.get(`/market/quote/${symbol}`),
  getSectors: () => api.get('/market/sectors'),
  getGlobalMarket: () => api.get('/market/global'),
}
```

### 国际化 (frontend/src/i18n/)
```
locales/
├── en.json   # 英文
└── zh.json   # 中文
```

---

## 8. 交易引擎

### MarcusVNPyExecutor
交易执行器类，负责：
1. **风控检查**: 资金检查、单笔仓位限制、持仓检查
2. **买入执行**: 扣款、增加持仓、记录日志
3. **卖出执行**: 计算盈亏(FIFO)、更新持仓
4. **数据持久化**: SQLite存储交易记录

### 风控规则
- 买入时资金充足性检查
- 单笔最大仓位限制 (40% 资金)
- 卖出时持仓检查
- 自动调整超过上限的仓位

### FIFO成本计算
卖出时按先进先出(FIFO)原则计算持仓成本和盈亏。

---

## 9. AI Agent 系统

### Function Calling 工具
Agent可调用的工具函数：

| 工具名 | 功能 |
|--------|------|
| get_market_indices | 获取主要指数 |
| get_quote | 查询个股行情 |
| get_sector_performance | 板块涨跌幅排名 |
| get_portfolio | 获取持仓 |
| get_account | 获取账户总览 |
| get_news | 财经新闻 |
| get_sentiment | 市场情绪分析 |
| execute_trade | 执行交易 |

### System Prompt
预设的系统提示词，定义Agent的角色定位和行为准则。

---

## 10. 任务调度系统

### 基于 APScheduler
- 使用 BackgroundScheduler
- CronTrigger 触发器
- 内存存储 (可切换到Redis/DB)

### 配置格式 (tasks.yaml)
```yaml
tasks:
  - id: unique-task-id
    name: 任务名称
    description: 任务描述
    enabled: true
    schedule:
      type: cron
      expr: "0 9,13,15 * * 1-5"  # 工作日 9:00, 13:00, 15:00
      timezone: Asia/Shanghai
    script:
      path: scripts/script.py
      args: [--arg1, value]
    notifications:
      channels: [qqbot]
      on_success: true
      on_failure: true
```

---

## 11. 数据库设计

### SQLite 数据库 (默认)

#### trades.db
```sql
-- 交易记录表
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    orderid TEXT,
    symbol TEXT,
    direction TEXT,  -- "买入" / "卖出"
    price REAL,
    volume INTEGER,
    amount REAL,
    profit REAL,    -- 卖出时的盈亏
    created_at TEXT
);

-- 账户信息表
CREATE TABLE account_info (
    id INTEGER PRIMARY KEY,
    initial_capital REAL,
    available_cash REAL,
    frozen_cash REAL
);
```

#### news.db
```sql
-- 新闻表
CREATE TABLE news (
    id INTEGER PRIMARY KEY,
    title TEXT,
    content TEXT,
    source TEXT,
    publish_time TEXT,
    sentiment TEXT,
    keyword TEXT,   -- 相关股票代码
    url TEXT
);
```

---

## 12. Docker 部署

### docker-compose.yml
定义4个服务：
1. **postgres**: PostgreSQL 15 数据库
2. **redis**: Redis 7 缓存
3. **backend**: FastAPI 后端 (端口8000)
4. **frontend**: Nginx 前端 (端口3000)

### 启动命令
```bash
cd docker
docker-compose up -d
```

### 环境变量
需在 `.env` 文件中配置：
```bash
DEEPSEEK_API_KEY=xxx
TUSHARE_TOKEN=xxx
XUEQIU_TOKEN=xxx
```

---

## 13. 移植指南

### 步骤1: 复制核心目录
```bash
# 复制以下目录到新项目
cp -r backend/app/api       newproject/backend/app/
cp -r backend/app/models    newproject/backend/app/
cp -r backend/app/services  newproject/backend/app/
cp -r backend/app/core      newproject/backend/app/
cp -r backend/app/agent    newproject/backend/app/
cp -r frontend/src/pages    newproject/frontend/src/
cp -r frontend/src/components newproject/frontend/src/
cp -r frontend/src/api      newproject/frontend/src/
cp -r frontend/src/i18n     newproject/frontend/src/
```

### 步骤2: 安装依赖
```bash
# 后端
pip install -r backend/requirements.txt

# 前端
cd frontend && npm install
```

### 步骤3: 配置环境变量
创建 `.env` 文件并配置必要的API密钥。

### 步骤4: 启动服务
```bash
# 后端
cd backend && python -m uvicorn app.main:app --reload

# 前端
cd frontend && npm run dev
```

### 注意事项
1. **路径配置**: `backend/app/config.py` 中的工作区路径需要正确设置
2. **依赖技能**: 某些功能依赖 `skills/` 目录下的模块 (xueqiu-data-query, akshare-news, vnpy-paper-trading)
3. **数据库初始化**: 首次运行需要初始化SQLite数据库
4. **CORS配置**: 生产环境需要配置正确的CORS源

---

## 14. 常见问题

### Q: 行情数据获取失败
A: 检查 `XUEQIU_TOKEN` 是否正确配置，xueqiu-data-query 技能是否在 `skills/` 目录。

### Q: AI Agent 不响应
A: 确认 `DEEPSEEK_API_KEY` 已配置且有效。

### Q: 交易执行被拒绝
A: 检查风控规则：资金是否充足、是否超过单笔最大仓位、是否持有该股票。

### Q: 调度任务未执行
A: 检查 `config/tasks.yaml` 配置、Cron表达式是否正确、脚本路径是否存在。

---

## 15. 文件清单

### 后端核心文件
```
backend/
├── app/
│   ├── __init__.py
│   ├── main.py                 # FastAPI 入口
│   ├── config.py               # 配置管理
│   ├── api/
│   │   ├── __init__.py
│   │   ├── portfolio.py         # 账户API
│   │   ├── trades.py           # 交易API
│   │   ├── market.py           # 行情API
│   │   ├── news.py             # 新闻API
│   │   ├── strategy.py         # 策略API
│   │   ├── scheduler.py        # 调度API
│   │   └── agent.py            # AI Agent API
│   ├── models/
│   │   ├── __init__.py
│   │   ├── account.py
│   │   ├── trade.py
│   │   ├── market.py
│   │   ├── news.py
│   │   └── strategy.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── scheduler_service.py
│   ├── core/
│   │   └── trading/
│   │       ├── marcus_trade.py
│   │       ├── paper_engine.py
│   │       └── ...
│   └── agent/
│       ├── __init__.py
│       ├── session.py
│       ├── storage.py
│       └── ...
└── requirements.txt
```

### 前端核心文件
```
frontend/
├── src/
│   ├── main.tsx
│   ├── App.tsx
│   ├── api/
│   │   └── client.ts
│   ├── components/
│   │   ├── Layout.tsx
│   │   └── LanguageSwitcher.tsx
│   ├── pages/
│   │   ├── PortfolioPage.tsx
│   │   ├── TradingPage.tsx
│   │   ├── MarketPage.tsx
│   │   ├── NewsPage.tsx
│   │   ├── StrategyPage.tsx
│   │   ├── AnalyticsPage.tsx
│   │   ├── SchedulerPage.tsx
│   │   └── TradingAgentPage.tsx
│   └── i18n/
│       ├── index.ts
│       └── locales/
│           ├── en.json
│           └── zh.json
├── package.json
├── vite.config.ts
├── tailwind.config.js
└── tsconfig.json
```

### 配置文件
```
config/
└── tasks.yaml

docker/
├── docker-compose.yml
├── Dockerfile.backend
└── Dockerfile.frontend

.env.example
```