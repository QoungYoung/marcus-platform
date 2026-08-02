# Marcus AI Trading Platform

A-share quantitative trading platform with AI-powered trade decisions, automated background monitors, and backtest engine.

## §1 Quick Navigation

Find code by keyword — no need to read source files.

### Trading & Orders

| Keyword | File(s) |
|---------|---------|
| 下单/执行交易 | `backend/app/api/trades.py` |
| 止损/stop loss | `backend/app/services/stop_loss_monitor.py`, `backend/app/core/trading/marcus_trade.py` |
| 加仓/tier/三级加仓 | `backend/app/services/position_tier_monitor.py` |
| 交易原因/reason | `backend/app/models/trade.py` (TradeRequest), `backend/app/api/trades.py` |
| 作废交易/void | `backend/app/api/trades.py` (void_trade) |
| T+0违规 | `backend/app/models/backtest_orm.py` (BacktestTrade.is_t0_violation) |

### Portfolio & Account

| Keyword | File(s) |
|---------|---------|
| 持仓/positions | `backend/app/api/portfolio.py`, `backend/app/models/account.py` |
| 账户摘要/account | `backend/app/api/portfolio.py` (get_portfolio) |
| 权益曲线/equity | `backend/app/api/portfolio.py` (get_equity_history) |
| 峰值权益/peak | `backend/app/core/peak_equity.py`, `backend/app/models/system_state.py` |
| 每周盈亏/weekly P&L | `backend/app/api/portfolio.py`, `frontend/src/pages/PortfolioPage.tsx` |
| 资金解冻/unfreeze | `backend/app/api/portfolio.py` (unfreeze_funds) |

### Market Data & Indicators

| Keyword | File(s) |
|---------|---------|
| 行情/K线/kline | `backend/app/api/market.py` (20 endpoints), `backend/app/models/market.py` |
| 技术指标/MA/MACD/RSI | `backend/app/api/indicator.py`, `backend/app/models/indicator.py` |
| 资金流向/moneyflow | `backend/app/api/market.py`, `backend/app/models/fund_flow_cache.py` |
| 板块/概念/sector | `backend/app/api/market.py` (concept-fund-flow, sector-flow) |
| 全球行情/global | `backend/app/api/market.py` (get_global_market) |
| 入场过滤器/entry filter | `backend/app/api/indicator.py` (check_entry_filters) |
| 仓位计算/calc position | `backend/app/api/indicator.py` (calc_position) |
| 日内数据/intraday | `backend/app/api/market.py` (get_intraday_min) |
| ETF | `backend/app/api/etf.py` |

### Backtest

| Keyword | File(s) |
|---------|---------|
| 回测任务/backtest task | `backend/app/api/backtest.py`, `backend/app/models/backtest_orm.py` |
| 回测引擎/engine | `backend/app/services/backtest_engine.py` |
| 回测止损 | `backend/app/services/backtest_stop_loss.py` |
| 回测监控/minute-by-minute | `backend/app/services/backtest_monitors.py` |
| 回测执行器/paper engine | `backend/app/core/trading/backtest_paper.py` |
| 本地数据提供者/local data | `backend/app/services/local_data_provider.py` |
| SSE流/stream | `backend/app/api/backtest.py` (stream_backtest) |
| CSV导出/export | `backend/app/api/backtest.py` (export_*_csv) |
| 沙盒/sandbox | `backend/app/api/backtest.py` (~20 sandbox endpoints) |
| 策略报告/strategy report | `backend/app/api/backtest.py` (get_strategy_report) |

### Scheduler & Background Monitors

| Keyword | File(s) |
|---------|---------|
| 定时任务/scheduler | `backend/app/services/scheduler_service.py`, `backend/app/api/scheduler.py` |
| 候选池/candidate pool | `backend/app/services/candidate_pool.py`, `backend/app/services/candidate_pool_monitor.py` |
| 长期池/long-term pool | `backend/app/services/long_term_pool.py`, `backend/app/services/long_term_pool_monitor.py` |
| 交易图谱/trade graph | `backend/app/services/trade_graph.py` |

### AI Agent & Panel

| Keyword | File(s) |
|---------|---------|
| AI聊天/agent chat | `backend/app/api/agent.py`, `backend/app/agent/` |
| 专家组/panel/reflect | `backend/app/api/panel.py` |
| 会话管理/session | `backend/app/agent/session.py`, `backend/app/agent/storage.py` |
| Prompt管理 | `backend/app/services/prompt_service.py`, `backend/app/models/prompt.py` |
| Pi Server | `servers/` (Node.js, port 3001) |

### Frontend

| Keyword | File(s) |
|---------|---------|
| 页面路由/routes | `frontend/src/App.tsx` |
| API客户端 | `frontend/src/api/client.ts` |
| K线图组件 | `frontend/src/components/KlineChart.tsx` |
| 主题/theme | `frontend/src/store/themeStore.ts` |
| 语言/i18n | `frontend/src/store/languageStore.ts`, `frontend/src/i18n/` |

### Config & Infrastructure

| Keyword | File(s) |
|---------|---------|
| 环境变量/config | `backend/app/config.py` (Settings class) |
| 数据库配置 | `backend/app/database.py` (SessionLocal, init_db) |
| Docker部署 | `docker/docker-compose.yml` |
| 启动脚本 | `scripts/start_backend.bat`, `scripts/start_frontend.bat` |
| 新闻/情绪分析 | `backend/app/api/news.py` |

---

## §2 Core Data Flows

### Trade Execution Flow

```
Frontend (TradingPage.tsx)
  │ POST /api/v1/trades  { symbol, side, price, volume, reason }
  ▼
backend/app/api/trades.py  execute_trade()
  │ 写入 SQLite trades.db
  │ 调用 MarcusVNPyExecutor
  ▼
backend/app/core/trading/marcus_trade.py  MarcusVNPyExecutor
  │ buy() / sell()
  │ 更新 VNPy paper engine 持仓
  │ 更新 account_info 表
  ▼
SQLite trades.db  ← 交易记录持久化
QQ Bot 通知 (qqbot_service.send_notification)
```

### Market Data Flow

```
外部数据源                             内部消费
┌──────────┐  tushare API    ┌──────────────────────┐
│ Tushare  │────────────────▶│ market.py API         │
└──────────┘                 │ indicator.py API      │
┌──────────┐  HTTP           │ stop_loss_monitor     │
│ Xueqiu   │────────────────▶│ position_tier_monitor │
└──────────┘                 │ backtest_engine       │
┌──────────┐                 └──────┬───────────────┘
│ AKShare  │                        │
└──────────┘                 ┌──────▼───────────────┐
                             │ PostgreSQL cache      │
                             │ fund_flow_cache       │
                             │ market_diagnosis      │
                             └──────────────────────┘
```

### Background Monitor Lifecycle

```
main.py lifespan startup
  │
  ├─▶ StopLossMonitor(executor).start()
  │      daemon thread, 31s polling
  │      checks positions → sell if triggered
  │      logs to stop_loss_log (PostgreSQL)
  │
  ├─▶ PositionTierMonitor(executor).start()
  │      daemon thread, 33s polling
  │      3-tier: probe → confirm → sprint
  │      7-gate arbitration before execution
  │
  ├─▶ CandidatePoolMonitor(executor).start()
  │      daemon thread, 37s polling
  │      auto-executes when entry filters pass
  │
  └─▶ LongTermPoolMonitor(executor).start()
         daemon thread, 300s (5min) polling

main.py lifespan shutdown
  │ 依次调用 .stop() → 设置 _stop_event → 线程退出
```

---

## §3 Module Quick Reference

### API Routes (`backend/app/api/`)

| File | Router Prefix | Tag | Endpoints | Key Handler Functions |
|------|-------------|-----|-----------|----------------------|
| `backtest.py` | `/backtest` | Backtest | 38 | create_backtest, start_backtest, stream_backtest, export_*_csv, sandbox_* |
| `market.py` | `/market` | Market Data | 20 | get_market_indices, get_stock_quote, get_stock_kline, get_moneyflow_mkt, get_concept_fund_flow |
| `scheduler.py` | `/scheduler` | Scheduler | 19 | get_scheduler_status, get_tasks, enable_task, trigger_task, stop-loss-monitor/* |
| `indicator.py` | `/indicator` | Technical Indicators | 12 | calculate_fibonacci, get_realtime_indicators, check_entry_filters, calc_position |
| `etf.py` | `/etf` | ETF Data | 8 | sync_etf_pool, get_etf_list, get_etf_kline |
| `trades.py` | `/trades` | Trades | 8 | execute_trade, get_trade_history, void_trade, unvoid_trade |
| `portfolio.py` | `/portfolio` | Portfolio | 5 | get_portfolio, get_positions, get_equity_history, unfreeze_funds |
| `prompts.py` | `/prompts` | prompts | 5 | list_prompts_dict, get_prompt_by_name, upsert_prompt, delete_prompt |
| `lt_pool.py` | `/lt-pool` | Long-Term Pool | 5 | list_candidates, add_candidate, remove_candidate, get_monitor_status |
| `agent.py` | `/agent` | Trading Agent | 4 | chat, list_tools, list_skills, analyze_stock |
| `pool.py` | `/pool` | Candidate Pool | 4 | list_candidates, add_candidate, remove_candidate, refresh_pool |
| `scan.py` | `/scan` | Scan | 4 | get_latest_scan_report, get_pi_analysis_history, get_trade_history |
| `db.py` | `/db` | database | 3 | query_table, get_schema, write_db |
| `news.py` | `/news` | News | 2 | get_news, get_market_sentiment |
| `panel.py` | (none) | panel | 2 | trigger_panel_reflect, trigger_panel_reflect_stream |
| `strategy.py` | `/strategy` | Strategy | 2 | get_current_strategy, get_scan_history |

### Services (`backend/app/services/`)

| File | Key Class | Pattern | Key Methods |
|------|-----------|---------|-------------|
| `stop_loss_monitor.py` | `StopLossMonitor` | Daemon thread, 31s | start, stop, status, get_position_stop_distances |
| `position_tier_monitor.py` | `PositionTierMonitor` | Daemon thread, 33s | start, stop, evaluate_position_tier, can_execute_add |
| `candidate_pool_monitor.py` | `CandidatePoolMonitor` | Daemon thread, 37s | start, stop, status |
| `long_term_pool_monitor.py` | `LongTermPoolMonitor` | Daemon thread, 300s | start, stop, status |
| `scheduler_service.py` | `SchedulerService` | APScheduler | start, stop, get_tasks, trigger_task, reload_config |
| `backtest_engine.py` | `BacktestEngine` | Async loop | run, cancel_task, get_trade_days |
| `qqbot_service.py` | `QQBotService` | Async WebSocket | start, stop, send_notification |
| `trade_graph.py` | `TradeState` (TypedDict) | LangGraph | build_graph, run_trade_decision |
| `candidate_pool.py` | `CandidatePool` | JSON persisted | get_waiting, get_ready, mark_promoted, refresh_all_sync |
| `long_term_pool.py` | `LongTermPool` | SQLite persisted | get_active, add, mark_promoted, reset_to_active |
| `local_data_provider.py` | `LocalDataProvider` | Lazy cache | load, get_daily_quote, get_moneyflow, get_sector_intraday_bias |
| `prompt_service.py` | (standalone fns) | — | get_prompt, upsert_prompt, seed_prompts |
| `backtest_stop_loss.py` | (standalone fns) | — | check_backtest_stop_loss |
| `backtest_monitors.py` | (standalone fns) | Sync while loop | run_minute_by_minute, capture_candidate |
| `improvement_tracker.py` | (standalone fns) | JSON file | add_improvement, mark_resolved, get_stats |

### Core Trading (`backend/app/core/trading/`)

| File | Key Class | Purpose |
|------|-----------|---------|
| `marcus_trade.py` | `MarcusVNPyExecutor` | Live trade executor wrapping VNPy paper engine |
| `backtest_paper.py` | `BacktestPaperEngine` | Backtest sandbox account engine |
| `_tech_divergence.py` | (standalone fns) | 5-signal technical divergence detection |
| `_60min_analysis.py` | (standalone fns) | 60-minute K-line stop analysis |

### Frontend Components

| File | Purpose |
|------|---------|
| `Layout.tsx` | Main layout wrapper with sidebar |
| `TopNav.tsx` | Top navigation bar |
| `KlineChart.tsx` | K-line/candlestick chart (ECharts) |
| `AgentSidebar.tsx` | AI agent chat sidebar |
| `ChatContainer.tsx` | Agent chat message container |
| `StockDetailPanel.tsx` | Stock detail popup/panel |
| `CronEditor.tsx` | Cron expression editor for scheduler |
| `LanguageSwitcher.tsx` | EN/ZH language toggle |

---

## §4 Database Schema

### PostgreSQL Tables (SQLAlchemy ORM)

| Table | Model Class | Defining File | Key Columns |
|-------|-------------|---------------|-------------|
| `prompts` | `Prompt` | `models/prompt.py` | name (unique), content (Text), version, is_active |
| `stop_loss_log` | `StopLossLog` | `models/stop_loss_log.py` | symbol, rule, price, realized_profit, executed |
| `fund_flow_cache` | `FundFlowCache` | `models/fund_flow_cache.py` | data_type, symbol, data_json (JSON blob) |
| `market_diagnosis` | `MarketDiagnosis` | `models/market_orm.py` | trade_date (PK), state, score_trend, score_oscillation |
| `system_state` | `SystemState` | `models/system_state.py` | key (PK), value, updated_at |
| `backtest_tasks` | `BacktestTask` | `models/backtest_orm.py` | name, start_date, end_date, initial_capital, status |
| `backtest_daily_logs` | `BacktestDailyLog` | `models/backtest_orm.py` | task_id (FK), trade_date, phase, event_type, content |
| `backtest_trades` | `BacktestTrade` | `models/backtest_orm.py` | task_id (FK), symbol, direction, price, profit, is_t0_violation |
| `backtest_positions` | `BacktestPosition` | `models/backtest_orm.py` | task_id (FK), trade_date, symbol, volume, market_value |
| `backtest_equity_snapshots` | `BacktestEquitySnapshot` | `models/backtest_orm.py` | task_id (FK), trade_date, total_asset, cumulative_return |
| `backtest_monthly_metrics` | `BacktestMonthlyMetric` | `models/backtest_orm.py` | task_id (FK), month, return_pct, win_rate, max_drawdown |

### SQLite Tables (raw CREATE TABLE)

| Table | Database | Defining Location | Purpose |
|-------|----------|-------------------|---------|
| `trades` | `data/trades.db` | Live paper trading | All executed trades |
| `account_info` | `data/trades.db` | Auto-created | Current account state |
| `daily_snapshot` | `data/trades.db` | `api/portfolio.py` | Daily equity snapshots |
| `long_term_candidates` | `data/cache.db` | `services/long_term_pool.py` | Long-term watchlist |
| `stock_pool` | `data/stock_pool.db` | Concept/industry stock pool | Stock-to-sector mapping |
| `news` | `data/news.db` | News cache | Financial news articles |

### Schema Patches (15 ALTER TABLE in `database.py`)

All applied idempotently via `ADD COLUMN IF NOT EXISTS`. Three backtest tables received additional columns for: trade detail export (stamp_tax, transfer_fee, slippage_pct, net_profit), T+0 violation flags, and AI model selection (model_name, thinking_level).

---

## §5 Frontend Architecture

### Route → Page Mapping

```
/                  → redirect to /portfolio
/portfolio         → PortfolioPage.tsx    (持仓、账户摘要、权益曲线)
/trading           → TradingPage.tsx      (下单、交易历史)
/market            → MarketPage.tsx       (行情、板块、K线)
/news              → NewsPage.tsx         (新闻、情绪)
/backtest          → BacktestPage.tsx     (回测管理)
/analytics         → AnalyticsPage.tsx    (分析报告)
/scheduler         → SchedulerPage.tsx    (定时任务)
/agent             → TradingAgentPage.tsx (AI 对话)
```

All routes rendered inside `<Layout />` (shared sidebar + nav shell).

### Zustand Stores

| Store | File | State |
|-------|------|-------|
| `useThemeStore` | `store/themeStore.ts` | theme: 'dark' \| 'light', toggleTheme() |
| `useLanguageStore` | `store/languageStore.ts` | language: 'zh' \| 'en', toggleLanguage() |

Both persisted to localStorage via `zustand/middleware persist`.

### API Client (`api/client.ts`)

Base URL `/api/v1`, Axios with 30s timeout. 8 API namespaces:
`portfolioApi`, `tradesApi`, `marketApi`, `newsApi`, `strategyApi`, `backtestApi`, `schedulerApi`, `healthApi`

### Tech Stack

React 18, TypeScript 5, Vite 5, Tailwind CSS 4, Zustand 4, ECharts 6, Recharts 2, i18next 26, Lucide React
