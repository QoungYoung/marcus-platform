## Context

The portfolio page (`PortfolioPage.tsx`) currently fetches 5 data sources on mount: portfolio summary, market indices, equity history (60 days), recent trades (8), and stop-loss status. Daily P&L breakdown (30 days) is fetched on demand when the user clicks a date in the modal. All data fetching uses bare `useEffect` + `useState` — no React Query or SWR.

The page layout follows a section-group structure defined in the recent Hallmark redesign:
```
┌─ cp-section-group 1 ──────────────────────────┐
│  hero (total asset + P&L)                      │
│  risk strip (4 cards: position ratio, etc.)     │
└────────────────────────────────────────────────┘
┌─ cp-section-group 2 ──────────────────────────┐
│  stop-loss monitor                             │
└────────────────────────────────────────────────┘
┌─ cp-section-group 3 ──────────────────────────┐
│  equity chart (left) + position pie (right)     │
│  positions table                               │
│  recent trades table                           │
└────────────────────────────────────────────────┘
```

All four new features consume data already fetched or easily derivable. The design constraint is to add meaningful analytics without cluttering the page or changing the existing section rhythm.

## Goals / Non-Goals

**Goals:**
- Add benchmark comparison toggle to the existing equity curve chart
- Add a "performance metrics" card row (Sharpe ratio, monthly return, quarterly return, win rate)
- Add stock P&L contribution ranking as a horizontal bar chart below positions
- Keep all computation client-side — zero backend changes
- Maintain the existing section-group layout structure
- Respect the Hallmark theme (Midnight technical, atmospheric genre, token discipline)

**Non-Goals:**
- No new API endpoints
- No new npm dependencies
- No changes to data fetching architecture (no React Query migration)
- No sector concentration (requires backend work — Phase 2)
- No quick trade shortcuts (separate UI concern — Phase 2)

## Decisions

### D1: Performance metrics placement — card row below hero

Place 4 small metric cards (Sharpe, monthly return, quarterly return, win rate) in a horizontal row between the hero and the risk strip, inside `cp-section-group 1`.

**Rationale:** The hero shows "what you have" (asset, P&L). The metric row shows "how well you're trading" (risk-adjusted return, consistency). They form a natural top-down reading flow. Placing them in the same section group as hero+risk keeps the overview together.

**Alternative considered:** Placing metrics at the bottom as a footer row. Rejected — metrics should be seen immediately, not after scrolling past positions.

### D2: Benchmark overlay — toggle on existing chart, not a new chart

Add a toggle button ("vs 沪深300") to the equity curve chart header. When active, a second `Line` series renders on the same `ComposedChart`, normalized so both curves start at 100 at the earliest date.

**Rationale:** A second chart would consume vertical space and create visual fragmentation. Overlaying on the same chart lets users directly compare slope and divergence. Recharts `ComposedChart` already supports multiple `Line` children — no new component needed.

**Data:** `marketApi.getIndices()` returns 5 indices. Use `SH000300` (沪深300) as the default benchmark. However, the indices endpoint returns a snapshot, not a history. For proper normalization, we need the index value at the start of the equity history window. Since we don't have historical index data via the current API, use a simplified approach: normalize both curves to 100 at day 0 using the first equity value and the current index value adjusted by each day's change_pct (which we also don't have historically).

**Resolution:** The market indices endpoint only returns current snapshot. To do proper historical benchmark overlay, we would need index history — which requires a new API endpoint or Tushare data. For Phase 1, implement a **simplified benchmark**: fetch the index's current value, assume it moves proportionally to the account's daily returns for visualization purposes, and mark it clearly as "approximate." Alternatively, skip the full curve overlay and instead show a **single benchmark comparison metric**: "沪深300同期: +X.X%" calculated by comparing the index change over the same period using available data.

**Revised decision:** Show benchmark as a **summary metric card** in the performance row (e.g., "沪深300 同期 +3.2%") rather than a curve overlay. Compute by comparing current index value to its value N days ago. Since we lack index history API, use a workaround: store the index value when the component mounts and compute the return over the visible period. If this proves insufficient, defer full curve overlay to Phase 2 when a `/market/index-history` endpoint can be added.

Wait — actually the simplest viable approach: the page already has `tickers` (current index data). The equity history has 60 data points. We can compute "return over this period" for the account. For the benchmark, we need the index's return over the same 60-day window. Without historical index data, the best we can do today is show the **account's absolute performance metrics** (Sharpe, monthly returns) and leave benchmark curve overlay for when index history is available.

**Final decision for Phase 1:** Include benchmark comparison as a metric card showing **daily alpha** (account daily return - estimated index daily return) using the index change_pct from the snapshot as a rough daily proxy. If the data quality is too low, show account-only metrics (Sharpe, monthly returns, win rate) and add a placeholder card "沪深300对比 — 需要历史数据" that clearly signals this will come in Phase 2.

Actually, I'm overcomplicating this. The simplest correct approach: the `marketApi.getIndices()` returns current index data with `change_pct` (today's change). For a benchmark comparison card, show **today's relative performance**: "今日跑赢/跑输沪深300 X.XX%". This is accurate (both data points exist), immediately useful, and requires zero assumptions.

For the full historical overlay, we'll add a `/market/index-history` endpoint in Phase 2.

### D3: P&L contribution — horizontal bar chart below positions table

Add a new row in `cp-section-group 3` below the positions table showing a horizontal bar chart of top 10 stocks by absolute P&L contribution over the last 30 days.

**Computation:** Aggregate `dailyPnlBreakdown` stocks array across all fetched days. Sum `float_pnl + realized_pnl` per symbol. Sort descending by absolute value. Take top 10.

**Visual:** Horizontal bars with green (positive) / red (negative) coloring. Stock symbol as label. Contribution amount formatted as ¥X.XX万.

**Rationale:** A horizontal bar chart is the standard finance visualization for contribution attribution. Recharts `BarChart` with `layout="vertical"` handles this natively.

### D4: Sharpe ratio computation

```
dailyReturns = equityHistory.map((point, i) => {
  if (i === 0) return 0
  return (point.equity - equityHistory[i-1].equity) / equityHistory[i-1].equity
}).slice(1)

meanReturn = dailyReturns.reduce((a,b) => a+b, 0) / dailyReturns.length
variance = dailyReturns.reduce((a,b) => a + (b-meanReturn)**2, 0) / dailyReturns.length
stdReturn = Math.sqrt(variance)
sharpe = stdReturn > 0 ? (meanReturn / stdReturn) * Math.sqrt(252) : 0
```

Risk-free rate assumed 0% (standard for retail A-share traders comparing strategies).

### D5: Monthly/quarterly returns

Group `equityHistory` by year-month. For each month with ≥5 data points, compute `(last.equity - first.equity) / first.equity`. Display as a compact table (3 columns: period, return%, indicator bar). Quarterly: group by year-quarter, same logic.

Display the last 6 months and last 4 quarters to keep the card compact.

## Risks / Trade-offs

- **No historical index data** → Benchmark curve overlay deferred to Phase 2. Phase 1 shows today's relative performance only. Mitigation: the "今日跑赢/跑输" card is still valuable and accurate.
- **Daily P&L breakdown not pre-fetched** → Currently only fetched when user clicks a date in the modal. Contribution ranking needs 30-day aggregate. Mitigation: add a `useEffect` to fetch full 30-day breakdown on mount. Adds one API call but the endpoint already exists.
- **Sharpe ratio with < 60 data points** → Statistically noisy with few data points. Mitigation: show "N/A" or a warning if < 20 data points available. For a 60-day window this is adequate.
- **Component bloat** → `PortfolioPage.tsx` is already large. Adding 4 features with inline state and computation will make it harder to maintain. Mitigation: extract metric computation into pure functions in a separate `portfolioMetrics.ts` helper file, keeping the component focused on rendering.
