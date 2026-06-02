# Marcus AI Trading Platform

<p align="center">
  <strong>🤖 AI 驱动的 A 股模拟交易平台</strong><br>
  实时行情 · 新闻情绪分析 · 策略管理 · 任务调度 · AI 助手
</p>

---

## 📖 简介

Marcus 是一个基于 AI 的 A 股模拟交易平台，提供从行情分析、策略制定到交易执行的完整链路。内置 DeepSeek AI 助手，支持自然语言查询行情、分析情绪、下达交易指令。

> ⚠️ 当前为 **Paper Trading（模拟交易）**，不涉及真实资金。

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────┐
│                     用户浏览器                        │
│                  localhost:5173                       │
└────────┬──────────────────┬──────────────────────────┘
         │                  │
         ▼                  ▼
┌─────────────┐    ┌──────────────┐    ┌──────────────┐
│  Frontend   │    │  Pi Server   │    │   Backend    │
│ React/Vite  │───▶│  Pi Agent    │───▶│   FastAPI    │
│  :5173      │    │  :3001       │    │   :8000      │
│ Tailwind    │    │  Tool Call   │    │   QQ Bot     │
└─────────────┘    └──────────────┘    └──────┬───────┘
                                               │
                                    ┌──────────┴───────┐
                                    │        ↓          │
                              ┌─────┴─────┐  ┌─────────┴──┐
                              │ PostgreSQL │  │   Redis    │
                              │   :5432    │  │   :6379    │
                              └───────────┘  └────────────┘
```

**三大核心服务：**

| 服务 | 技术栈 | 端口 | 说明 |
|------|--------|------|------|
| **Backend** | Python 3.12 + FastAPI | `8000` | REST API、QQ Bot、任务调度、交易引擎 |
| **Pi Server** | Node.js + TypeScript | `3001` | Pi Agent 桥接，Function Calling 工具调用 |
| **Frontend** | React 18 + Vite | `5173` | 仪表盘 UI，8 个功能页面 |

---

## ✨ 功能特性

### 📊 行情数据
- A 股指数、美股指数、大宗商品实时行情
- 板块涨跌幅排名与热点追踪
- 个股实时报价查询

### 💰 交易系统
- 模拟买入/卖出，支持 FIFO 成本计算
- 资金风控：单笔仓位限制、资金充足性检查
- 持仓管理、交易历史查询

### 📰 新闻情绪
- 财经新闻自动采集
- DeepSeek AI 情绪分析（看涨/看跌/中性）
- 关联股票关键词提取

### 🧠 AI Agent
- 基于 Function Calling 的智能助手
- 自然语言查询行情、持仓、执行交易
- 多轮对话，上下文感知

### ⏰ 任务调度
- Cron 定时任务配置
- 执行历史追踪
- 支持手动触发、启用/禁用
- QQ Bot 通知推送

### 🌐 前端仪表盘
- **Portfolio** — 账户总览 & 持仓管理
- **Trading** — 交易下单面板
- **Market** — 指数行情 & 板块热点
- **News** — 新闻流 & 情绪分析
- **Strategy** — 策略状态 & Watchlist
- **Analytics** — 收益图表 & 统计分析
- **Scheduler** — 任务调度管理
- **Agent** — AI 聊天助手

---

## 🚀 快速开始

### 环境要求

| 依赖 | 版本 |
|------|------|
| Python | 3.12+ |
| Node.js | 18+ |
| PostgreSQL | 15（可选，默认 SQLite） |
| Redis | 7（可选） |

### 方式一：一键启动（Windows）

```bash
# 双击运行项目根目录下的启动脚本
marcus.bat

# 菜单选项:
# [6] Install Dependencies  — 安装所有依赖
# [1] Start All Services     — 启动全部服务
```

### 方式二：手动启动

**1. 克隆项目**

```bash
git clone https://github.com/your-username/marcus-platform.git
cd marcus-platform
```

**2. 配置环境变量**

```bash
cp .env.example .env
# 编辑 .env，填入你的 API Keys
```

**3. 安装依赖**

```bash
# Backend
cd backend
pip install -r requirements.txt

# Frontend
cd ../frontend
npm install

# Pi Server
cd ../servers/pi-server
npm install
```

**4. 启动服务**

```bash
# 终端 1：Pi Server
cd servers/pi-server
npx tsx src/index.ts

# 终端 2：Backend
cd backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

# 终端 3：Frontend
cd frontend
npm run dev
```

**5. 访问**

| 服务 | 地址 |
|------|------|
| 前端仪表盘 | http://localhost:5173 |
| 后端 API 文档 | http://localhost:8000/docs |
| Pi Server 健康检查 | http://localhost:3001/health |

---

## 🐳 Docker 部署

```bash
# 1. 配置环境变量
cp .env.example .env
vim .env  # 填写 API Keys

# 2. 一键启动所有服务
cd docker
docker-compose --env-file ../.env up -d --build

# 3. 查看运行状态
docker-compose ps

# 4. 查看日志
docker-compose logs -f
```

Docker 会启动 5 个容器：`postgres`、`redis`、`backend`、`piserver`、`frontend`。

---

## 🔑 必需的 API Keys

| 变量 | 用途 | 获取地址 |
|------|------|----------|
| `DEEPSEEK_API_KEY` | AI 对话、情绪分析 | [platform.deepseek.com](https://platform.deepseek.com) |
| `TUSHARE_TOKEN` | A 股数据 | [tushare.pro](https://tushare.pro) |
| `XUEQIU_TOKEN` | 雪球实时行情 | 浏览器登录雪球后获取 Cookie |

完整环境变量见 [`.env.example`](./.env.example)。

---

## 📡 API 端点

Base URL: `http://localhost:8000/api/v1`

### 账户 & 交易

| 端点 | 方法 | 说明 |
|------|------|------|
| `/portfolio` | GET | 账户总览 |
| `/portfolio/positions` | GET | 当前持仓 |
| `/trades` | GET | 交易历史 |
| `/trades` | POST | 执行交易 |
| `/trades/{order_id}` | GET | 订单详情 |

### 行情 & 新闻

| 端点 | 方法 | 说明 |
|------|------|------|
| `/market/indices` | GET | A 股主要指数 |
| `/market/quote/{symbol}` | GET | 个股行情 |
| `/market/sectors` | GET | 板块涨跌幅 |
| `/market/global` | GET | 全球市场 |
| `/news` | GET | 新闻列表 |
| `/news/sentiment` | GET | 情绪分析 |

### 策略 & 调度

| 端点 | 方法 | 说明 |
|------|------|------|
| `/strategy/current` | GET | 当前策略 |
| `/strategy/scans` | GET | 扫描历史 |
| `/scheduler/status` | GET | 调度器状态 |
| `/scheduler/tasks` | GET | 任务列表 |
| `/scheduler/tasks/{id}/trigger` | POST | 触发任务 |

### AI Agent

| 端点 | 方法 | 说明 |
|------|------|------|
| `/agent/chat` | POST | AI 对话 |
| `/agent/tools` | GET | 可用工具列表 |

---

## 🗂️ 项目结构

```
marcus-platform/
├── backend/                    # Python 后端
│   ├── app/
│   │   ├── api/                # REST API 端点 (10 个模块)
│   │   ├── models/             # Pydantic 数据模型
│   │   ├── services/           # 业务服务层
│   │   ├── core/trading/       # 交易引擎
│   │   └── agent/              # AI Agent
│   ├── requirements.txt
│   └── main.py
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── pages/              # 页面组件 (8 个)
│   │   ├── components/         # 布局组件
│   │   ├── api/client.ts       # Axios 封装
│   │   └── i18n/               # 国际化 (中/英)
│   └── vite.config.ts
├── servers/pi-server/          # Pi Agent 服务
│   └── src/
│       ├── index.ts            # HTTP 服务器入口
│       └── tools.ts            # Function Calling 工具
├── docker/                     # Docker 配置
│   ├── docker-compose.yml
│   ├── Dockerfile.backend
│   ├── Dockerfile.piserver
│   ├── Dockerfile.frontend
│   └── nginx.conf
├── config/
│   └── tasks.yaml              # 调度任务配置
├── scripts/                    # 运维脚本
├── .env.example                # 环境变量模板
└── marcus.bat                  # Windows 一键启动
```

---

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 数据库 | PostgreSQL 15 / SQLite |
| 缓存 | Redis 7 |
| 任务调度 | APScheduler |
| 前端框架 | React 18 + TypeScript |
| 构建工具 | Vite 5 |
| CSS 框架 | Tailwind CSS 4 |
| 图表 | Recharts |
| 状态管理 | Zustand |
| 国际化 | i18next |
| AI 模型 | DeepSeek |
| 容器化 | Docker + Docker Compose |

---

## 📄 License

MIT

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request。

1. Fork 本项目
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request
