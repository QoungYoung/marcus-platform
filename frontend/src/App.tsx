import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import PortfolioPage from './pages/PortfolioPage'
import TradingPage from './pages/TradingPage'
import MarketPage from './pages/MarketPage'
import NewsPage from './pages/NewsPage'
import BacktestPage from './pages/BacktestPage'
import AnalyticsPage from './pages/AnalyticsPage'
import SchedulerPage from './pages/SchedulerPage'
import TradingAgentPage from './pages/TradingAgentPage'
import BullCalculatorPage from './pages/BullCalculatorPage'

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/portfolio" replace />} />
          <Route path="portfolio" element={<PortfolioPage />} />
          <Route path="trading" element={<TradingPage />} />
          <Route path="market" element={<MarketPage />} />
          <Route path="news" element={<NewsPage />} />
          <Route path="backtest" element={<BacktestPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="scheduler" element={<SchedulerPage />} />
          <Route path="agent" element={<TradingAgentPage />} />
          <Route path="calculator" element={<BullCalculatorPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
