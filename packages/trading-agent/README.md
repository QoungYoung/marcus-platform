# Marcus Trading Agent

基于 Pi (AI Coding Agent) 改造的股市分析与交易助手。

## 架构

```
marcus-platform/
├── packages/trading-agent/     # TypeScript Agent 包
│   ├── src/
│   │   ├── harness/           # Agent 运行时
│   │   ├── tools/             # 市场/交易工具
│   │   └── skills/            # 分析技能
│   └── package.json
├── backend/app/api/agent.py   # FastAPI Agent 端点
└── frontend/src/pages/TradingAgentPage.tsx  # Web 界面
```

## 功能

### Agent 技能

| 技能 | 描述 |
|------|------|
| market-analysis | 分析市场走势、板块热点、指数表现 |
| stock-research | 研究个股基本面和技术面 |
| trading-execute | 执行股票交易、管理订单 |
| portfolio-review | 审视投资组合、评估风险 |
| news-sentiment | 分析财经新闻和市场情绪 |

### 可用工具

| 工具 | API 端点 |
|------|----------|
| get_market_indices | GET /api/v1/market/indices |
| get_quote | GET /api/v1/market/quote/{symbol} |
| get_sector_performance | GET /api/v1/market/sectors |
| get_hot_stocks | GET /api/v1/market/hot |
| get_global_market | GET /api/v1/market/global |
| get_portfolio | GET /api/v1/portfolio/positions |
| get_account | GET /api/v1/portfolio |
| get_news | GET /api/v1/news |
| get_sentiment | GET /api/v1/news/sentiment |
| execute_trade | POST /api/v1/trades |

## 快速开始

### 1. 启动后端

```bash
cd backend
python run.py
```

### 2. 启动前端

```bash
cd frontend
npm install
npm run dev
```

### 3. 访问 Agent 页面

打开浏览器访问 http://localhost:3000/agent

## API 端点

### POST /api/v1/agent/chat

与 Agent 对话。

**请求:**
```json
{
  "message": "分析今日上证指数走势",
  "session_id": "optional-session-id"
}
```

**响应:**
```json
{
  "response": "今日上证指数表现...",
  "tool_calls": [...],
  "session_id": "default",
  "timestamp": "2024-01-01T00:00:00"
}
```

### GET /api/v1/agent/tools

列出可用工具。

### GET /api/v1/agent/skills

列出可用技能。

## 技术栈

- **Agent 框架**: Pi Agent Core (@earendil-works/pi-agent-core)
- **LLM**: DeepSeek API (via SiliconFlow)
- **后端**: FastAPI (Python)
- **前端**: React + TypeScript
- **数据源**: 雪球 (Xueqiu), AKShare