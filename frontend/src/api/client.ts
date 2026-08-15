import axios from 'axios'

const API_BASE = '/api/v1'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 60000,
})

// Account APIs
export const accountsApi = {
  list: () => api.get('/accounts'),
}

// Portfolio APIs
export const portfolioApi = {
  getSummary: (account = 'stock') => api.get('/portfolio', { params: { account } }),
  getPositions: (account = 'stock') => api.get('/portfolio/positions', { params: { account } }),
  getEquityHistory: (days = 60, account = 'stock') => api.get('/portfolio/equity-history', { params: { days, account } }),
  getDailyPnlBreakdown: (days = 30, account = 'stock') => api.get('/portfolio/daily-pnl-breakdown', { params: { days, account } }),
  getDailyPnlBreakdownByDate: (date: string, account = 'stock') => api.get('/portfolio/daily-pnl-breakdown/date', { params: { date, account } }),
  unfreeze: (account = 'stock') => api.post('/portfolio/unfreeze', null, { params: { account } }),
  adjustCapital: (data: { amount: number; note?: string }, account = 'stock') => api.post('/portfolio/adjust-capital', data, { params: { account } }),
}

// Trade APIs
export const tradesApi = {
  execute: (data: { symbol: string; side: string; price: number; volume: number; reason?: string; account?: string }) =>
    api.post('/trades', data),
  getHistory: (params?: { symbol?: string; limit?: number; page?: number; account?: string }) =>
    api.get('/trades', { params }),
  getTrade: (orderId: string) => api.get(`/trades/${orderId}`),
  voidTrade: (tradeId: number, reason: string, account = 'stock') =>
    api.post(`/trades/${tradeId}/void`, { reason }, { params: { account } }),
  unvoidTrade: (tradeId: number, account = 'stock') =>
    api.post(`/trades/${tradeId}/unvoid`, null, { params: { account } }),
  getVoidedTrades: (account = 'stock') => api.get('/trades/voided', { params: { account } }),
}

// Market APIs
export const marketApi = {
  getIndices: () => api.get('/market/indices'),
  getQuote: (symbol: string) => api.get(`/market/quote/${symbol}`),
  getSectors: () => api.get('/market/concept-fund-flow'),
  getGlobalMarket: () => api.get('/market/global'),
  getBreadth: () => api.get('/market/breadth'),
  getTopMovers: (params?: { type?: 'gainers' | 'losers' | 'active'; limit?: number }) =>
    api.get('/market/top-movers', { params }),
  getKline: (symbol: string, params?: { start_date?: string; end_date?: string; limit?: number }) =>
    api.get(`/market/kline/${symbol}`, { params }),
  getMoneyflow: (symbol: string, params?: { start_date?: string; end_date?: string; limit?: number }) =>
    api.get(`/market/moneyflow/${symbol}`, { params }),
  getTechnical: (symbol: string, params?: { start_date?: string; end_date?: string; limit?: number }) =>
    api.get(`/market/technical/${symbol}`, { params }),
  getProBar: (symbol: string, params?: { start_date?: string; end_date?: string; adj?: string; limit?: number }) =>
    api.get(`/market/pro-bar/${symbol}`, { params }),
  getIndustryLeaderboard: (params?: {
    limit?: number; sort_by?: string; industry?: string; refresh?: boolean; date?: string;
  }) => api.get('/market/industry-leaderboard', { params }),
  getForwardReturns: (symbol: string, date: string) =>
    api.get(`/market/forward-returns/${symbol}`, { params: { date } }),
}

// News APIs
export const newsApi = {
  getNews: (params?: { symbol?: string; limit?: number; page?: number }) =>
    api.get('/news', { params }),
  getSentiment: () => api.get('/news/sentiment'),
}

// Strategy APIs (legacy, kept for backward compatibility)
export const strategyApi = {
  getCurrent: () => api.get('/strategy/current'),
  getScanHistory: (params?: { limit?: number }) => api.get('/strategy/scans', { params }),
}

// Backtest APIs
export const backtestApi = {
  create: (data: { name: string; start_date: string; end_date: string; initial_capital: number; include_chinext?: boolean; model?: string; thinking_level?: string }) =>
    api.post('/backtest/create', data),
  start: (taskId: string) => api.post(`/backtest/${taskId}/start`),
  cancel: (taskId: string) => api.post(`/backtest/${taskId}/cancel`),
  getStreamUrl: (taskId: string) => `/api/v1/backtest/${taskId}/stream`,
  getDetail: (taskId: string) => api.get(`/backtest/${taskId}`),
  listTasks: (params?: { limit?: number; offset?: number }) =>
    api.get('/backtest/tasks', { params }),
  deleteTask: (taskId: string) => api.delete(`/backtest/${taskId}`),
  getTrades: (taskId: string, params?: {
    page?: number; page_size?: number; direction?: string;
    keyword?: string; start_date?: string; end_date?: string;
  }) => api.get(`/backtest/${taskId}/trades`, { params }),
  getEquityCsvUrl: (taskId: string) => `/api/v1/backtest/${taskId}/equity-csv`,
  getTradesCsvUrl: (taskId: string) => `/api/v1/backtest/${taskId}/trades-csv`,
  getPositionsCsvUrl: (taskId: string) => `/api/v1/backtest/${taskId}/positions-csv`,
  getIndexCsvUrl: (taskId: string) => `/api/v1/backtest/${taskId}/index-csv`,
  getExportAllUrl: (taskId: string) => `/api/v1/backtest/${taskId}/export-all`,
  getStrategyReport: async (taskId: string) => {
    const r = await api.get(`/backtest/${taskId}/strategy-report`)
    return r.data as { task_id: string; markdown: string; stats: any }
  },
}

// 做T回测 APIs（t-backtest，单标的 + 组合 + 自动选股）
export const tBacktestApi = {
  create: (data: {
    symbol?: string; symbols?: string[]; build_mode?: boolean; rolling_build?: boolean; build_limit_ratio?: number;
    select_source?: 'manual' | 'pool' | 'scan'; select_limit?: number;
    start_date: string; end_date: string; conditions?: any[]; init_shares?: number;
    net_asset?: number; review_mode?: 'llm' | 'rule';
  }) => api.post('/t/backtest', data),
  list: (limit = 50) => api.get('/t/backtest/tasks', { params: { limit } }),
  detail: (taskId: number) => api.get(`/t/backtest/${taskId}`),
  start: (taskId: number) => api.post(`/t/backtest/${taskId}/start`),
  cancel: (taskId: number) => api.post(`/t/backtest/${taskId}/cancel`),
  report: (taskId: number) => api.get(`/t/backtest/${taskId}/report`),
  events: (taskId: number, limit = 500) => api.get(`/t/backtest/${taskId}/events`, { params: { limit } }),
  candidates: (limit = 10) => api.get('/t/backtest/candidates', { params: { limit } }),
}

// 做T AI 主导 APIs（决策审计 / 选股结果）
export const tAiApi = {
  actions: (params?: { trade_date?: string; symbol?: string; limit?: number }) =>
    api.get('/t/ai/actions', { params }),
}

// Scheduler APIs
export const schedulerApi = {  getStatus: () => api.get('/scheduler/status'),
  getTasks: () => api.get('/scheduler/tasks'),
  getTask: (taskId: string) => api.get(`/scheduler/tasks/${taskId}`),
  getTaskExecutions: (taskId: string, limit?: number) =>
    api.get(`/scheduler/tasks/${taskId}/executions`, { params: { limit } }),
  getExecutionLog: (executionId: string) =>
    api.get(`/scheduler/executions/${executionId}/log`),
  triggerTask: (taskId: string) => api.post(`/scheduler/tasks/${taskId}/trigger`),
  enableTask: (taskId: string) => api.post(`/scheduler/tasks/${taskId}/enable`),
  disableTask: (taskId: string) => api.post(`/scheduler/tasks/${taskId}/disable`),
  updateTask: (taskId: string, data: { schedule?: { type: string; expr: string; timezone: string }; enabled?: boolean; notifications?: Record<string, unknown> }) =>
    api.patch(`/scheduler/tasks/${taskId}`, data),
  getNextRuns: () => api.get('/scheduler/next-runs'),
  reload: () => api.post('/scheduler/reload'),
  start: () => api.post('/scheduler/start'),
  stop: () => api.post('/scheduler/stop'),
  getStopLossMonitor: () => api.get('/scheduler/stop-loss-monitor'),
  startStopLossMonitor: () => api.post('/scheduler/stop-loss-monitor/start'),
  stopStopLossMonitor: () => api.post('/scheduler/stop-loss-monitor/stop'),
}

// Reflect Panel APIs
export const reflectApi = {
  getSessions: () => api.get('/panel/reflect/sessions'),
  getSession: (id: string) => api.get(`/panel/reflect/sessions/${id}`),
}

// Golden Pit APIs
export const goldenPitApi = {
  getScore: () => api.get('/golden-pit/score'),
  getFactors: () => api.get('/golden-pit/factors'),
  getStatus: () => api.get('/golden-pit/status'),
  getHistory: (index?: string, days?: number) =>
    api.get('/golden-pit/history', { params: { index: index || 'all', days: days || 60 } }),
  getSnapshots: (days?: number) =>
    api.get('/golden-pit/snapshots', { params: { days: days || 30 } }),
  getDisplayConfig: () => api.get('/golden-pit/display-config'),
  getSectorConfig: () => api.get('/golden-pit/sector-config'),
  updateSectorConfig: (values: Record<string, string | number | boolean>) =>
    api.put('/golden-pit/sector-config', { values }),
  getIndustryPreview: () => api.get('/golden-pit/industry-preview'),
  getTechStatus: (as_of?: string) =>
    api.get('/golden-pit/tech-status', { params: as_of ? { as_of } : {} }),
}

// Health check
export const healthApi = {
  check: () => api.get('/health'),
}

export default api
