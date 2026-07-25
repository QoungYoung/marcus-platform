## Why

The portfolio page currently displays raw account data (total asset, P&L, positions, equity curve) but lacks performance analytics that help users evaluate their trading quality. Users must mentally calculate whether they're beating the market, which stocks drive returns, or whether their risk-adjusted returns justify the volatility. Adding these metrics directly to the dashboard turns it from a passive balance viewer into an active performance diagnostic tool — all using data already fetched by the page.

## What Changes

- Add **benchmark comparison** overlay to the equity curve chart, normalizing the account equity curve against a major index (沪深300) on the same chart
- Add **stock P&L contribution ranking** below the positions table, showing which holdings contributed most to total P&L over the past 30 days as a horizontal bar chart
- Add **Sharpe ratio** to a new "performance metrics" row in the hero section, computed client-side from daily equity history
- Add **monthly/quarterly return breakdown** as a small table or sparkline alongside the Sharpe ratio

All four features are frontend-only — they consume data already returned by existing API endpoints (`/portfolio`, `/portfolio/equity-history`, `/portfolio/daily-pnl-breakdown`, `/market/indices`). No backend changes required.

## Capabilities

### New Capabilities
- `portfolio-performance-metrics`: Client-side computation and display of benchmark-relative performance, P&L attribution, risk-adjusted return (Sharpe), and calendar-period returns on the portfolio dashboard page

### Modified Capabilities
<!-- None — these are additive display features that don't change existing API contract or behavior -->

## Impact

- **Frontend**: `PortfolioPage.tsx` (new state slices, chart components, metric cards), `portfolio-page.css` (new styles for metric cards, ranking bars, benchmark toggle)
- **Backend**: None
- **APIs**: No new endpoints, no changes to existing endpoints
- **Dependencies**: No new packages — Recharts already provides `Line`/`Bar`/`ComposedChart` needed for benchmark overlay and contribution ranking
