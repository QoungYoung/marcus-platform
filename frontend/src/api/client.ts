import axios from 'axios'

const API_BASE = '/api/v1'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 30000,
})

// Portfolio APIs
export const portfolioApi = {
  getSummary: () => api.get('/portfolio'),
  getPositions: () => api.get('/portfolio/positions'),
  getEquityHistory: (days = 60) => api.get('/portfolio/equity-history', { params: { days } }),
  unfreeze: () => api.post('/portfolio/unfreeze'),
}

// Trade APIs
export const tradesApi = {
  execute: (data: { symbol: string; side: string; price: number; volume: number; reason?: string }) =>
    api.post('/trades', data),
  getHistory: (params?: { symbol?: string; limit?: number; page?: number }) =>
    api.get('/trades', { params }),
  getTrade: (orderId: string) => api.get(`/trades/${orderId}`),
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
}

// News APIs
export const newsApi = {
  getNews: (params?: { symbol?: string; limit?: number; page?: number }) =>
    api.get('/news', { params }),
  getSentiment: () => api.get('/news/sentiment'),
}

// Strategy APIs
export const strategyApi = {
  getCurrent: () => api.get('/strategy/current'),
  getScanHistory: (params?: { limit?: number }) => api.get('/strategy/scans', { params }),
}

// Scheduler APIs
export const schedulerApi = {
  getStatus: () => api.get('/scheduler/status'),
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
}

// Health check
export const healthApi = {
  check: () => api.get('/health'),
}

export default api
