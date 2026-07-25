## 1. Backend — Sector concentration

- [x] 1.1 In `backend/app/api/portfolio.py`, add a helper function `_compute_sector_concentration(positions)` that queries `stock_concept_map` from `stock_pool.db`, maps each position symbol to concept sectors, aggregates market_value by sector, and returns the sector breakdown dict
- [x] 1.2 Handle multi-concept stocks by distributing market_value equally across all concepts for that stock
- [x] 1.3 Wire the helper into the portfolio summary endpoint — populate `sector_concentration` in the `PortfolioSummary` response with `sectors`, `max_sector`, and `concentration_level`
- [x] 1.4 Handle edge cases: empty positions → null, symbol not in concept_map → "其他/未分类", single-concept stocks → no distribution needed

## 2. Frontend — Fund flow data layer

- [x] 2.1 Add `moneyflowMap` state (`Record<string, ThsMoneyflowRow>`) and loading state in `PortfolioPage.tsx`
- [x] 2.2 Add `useEffect` that fetches moneyflow for all positions via `Promise.allSettled(marketApi.getMoneyflow(symbol))` after positions are loaded
- [x] 2.3 Add a "刷新资金流" button in the positions panel header that re-fetches moneyflow data

## 3. Frontend — Fund flow badges + summary strip

- [x] 3.1 Render a fund flow badge on each position row: "主力流入" (green) / "主力流出" (red) / "平衡" (dim) / "—" (no data), using `main_net` magnitude vs 1% of market_value threshold
- [x] 3.2 Add a fund flow summary strip above the positions table header showing "X只流入 / Y只流出" or "全部流入/流出" or hidden if no data
- [x] 3.3 Add a new column header "资金流" in the table `<thead>` to label the badge column

## 4. Frontend — Inline quick trade panel

- [x] 4.1 Add `expandedTradeSymbol` and `tradeForm` state to `PortfolioPage.tsx` for tracking which row is expanded and the form values
- [x] 4.2 Add "+" and "−" icon buttons on each position row, visible on hover, that set `expandedTradeSymbol` and pre-fill `tradeForm`
- [x] 4.3 Render an inline expandable row (`<tr>` + `<td colSpan={...}>`) below the clicked position row with: direction tag (买/卖), price input (pre-filled), volume input, reason textarea, submit/cancel buttons
- [x] 4.4 On submit: validate inputs, call `tradesApi.execute()`, show success alert, collapse panel, and refresh portfolio summary
- [x] 4.5 Handle validation errors (price ≤ 0, volume ≤ 0, sell volume > holding) with inline error text
- [x] 4.6 Handle API errors by displaying the error message inline without collapsing the panel

## 5. Frontend — Sector concentration display

- [x] 5.1 Add a sector concentration section in `cp-section-group 3` (alongside charts) using a Recharts donut chart (`PieChart` with `innerRadius`/`outerRadius`)
- [x] 5.2 Read `sector_concentration` from the portfolio summary response and extract sector data
- [x] 5.3 Highlight the dominant sector (>50%) with a visual warning (amber accent border or annotation)
- [x] 5.4 Show "暂无板块数据" placeholder when sector data is null or unavailable

## 6. CSS styling

- [x] 6.1 Add `.cp-trade-panel` styles (inline expanded row, form inputs, submit button) to `portfolio-page.css` using `var(--agent-*)` tokens
- [x] 6.2 Add `.cp-flow-badge` styles (green/red/dim variants) for fund flow badges
- [x] 6.3 Add `.cp-flow-summary` styles for the aggregated flow strip
- [x] 6.4 Add `.cp-sector-chart` styles for the sector concentration donut section
- [x] 6.5 Add responsive breakpoints for all new components

## 7. Build and verify

- [x] 7.1 Run backend syntax check: `python -m py_compile backend/app/api/portfolio.py`
- [x] 7.2 Run frontend build: `npm run build` in `frontend/` — confirm zero TypeScript errors
- [ ] 7.3 Visually verify: inline trade panel opens/closes correctly, fund flow badges show correct colors, sector donut renders with data (or placeholder if no data)
