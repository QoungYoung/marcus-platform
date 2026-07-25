## 1. Metrics computation helper

- [x] 1.1 Create `frontend/src/pages/portfolioMetrics.ts` with pure functions: `computeSharpeRatio(equityHistory)`, `computeMonthlyReturns(equityHistory)`, `computeQuarterlyReturns(equityHistory)`, `computeBenchmarkDelta(accountReturn, indexChangePct)`, `aggregatePnlContributions(dailyBreakdown)`
- [x] 1.2 Add TypeScript interfaces for metric results (`MetricCard`, `PnlContribution`, `PeriodReturn`) in the helper file

## 2. Data fetching

- [x] 2.1 Add `useEffect` in `PortfolioPage.tsx` to fetch 30-day daily P&L breakdown on mount via `portfolioApi.getDailyPnlBreakdown(30)` — store in new `breakdowns` state
- [x] 2.2 Extract the 沪深300 index from existing `tickers` state for benchmark computation

## 3. Performance metric cards

- [x] 3.1 Add metric computation calls in `PortfolioPage.tsx` using `useMemo` (Sharpe, monthly returns, quarterly returns, benchmark delta) derived from existing `realEquity`, `summary`, and `tickers` state
- [x] 3.2 Add a `.cp-metrics-row` div between hero and risk strip inside `cp-section-group 1`, rendering 4 metric cards: Sharpe ratio, 本月收益, 本季收益, 今日跑赢/跑输沪深300
- [x] 3.3 Each card shows: label (top), value (large number), subtitle (green/red indicator where applicable)

## 4. P&L contribution ranking

- [x] 4.1 Add a new `.cp-contribution-section` div in `cp-section-group 3` below the positions table
- [x] 4.2 Render a Recharts horizontal `BarChart` with top 10 stocks by absolute contribution, green/red bars, and formatted ¥ labels
- [x] 4.3 Handle edge cases: empty breakdown data (show placeholder), < 10 stocks (render all without padding)

## 5. CSS styling

- [x] 5.1 Add `.cp-metrics-row`, `.cp-metric-card`, `.cp-metric-label`, `.cp-metric-value`, `.cp-metric-sub` styles to `portfolio-page.css` — use existing `var(--agent-*)` tokens only
- [x] 5.2 Add `.cp-contribution-section` and `.cp-contribution-bar` styles — match existing chart container visual language
- [x] 5.3 Add responsive breakpoints for metrics row (4 columns → 2 columns → 1 column at mobile widths 768/414/375/320)
- [x] 5.4 Add skeleton/placeholder styles for loading states

## 6. Build and verify

- [x] 6.1 Run `npm run build` in `frontend/` — confirm zero TypeScript errors and successful Vite build
- [ ] 6.2 Visually verify all 4 metric cards render with correct values, contribution chart renders with correct data, and responsive layout works at 414px and 768px widths
