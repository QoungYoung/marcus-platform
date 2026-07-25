## Why

Phase 1 added performance analytics (Sharpe, benchmark, contribution ranking) to the portfolio page. But the page still lacks two critical dimensions of a complete trading dashboard: **actionability** (can the user act on what they see?) and **risk context** (are positions concentrated in one sector? is smart money flowing in or out?). These three features close the gap — making the portfolio page not just informative but actionable.

## What Changes

- Add **quick trade shortcuts** on position rows — hover reveals buy/sell buttons that open a compact inline trading panel, calling the existing `POST /api/v1/trades` endpoint. No navigation away from the portfolio page required.
- Add **fund flow summary** — fetch per-stock moneyflow data for all positions and display an aggregated "主力态度" signal (net inflow/outflow, main force direction) as a summary strip or badge on each position row.
- Add **sector concentration** — populate the existing but unused `sector_concentration` field in the portfolio API response using `stock_pool.db`'s `stock_concept_map` table, and render a sector concentration breakdown on the frontend as a horizontal bar or donut chart.

## Capabilities

### New Capabilities
- `portfolio-quick-trade`: Inline trading panel on position rows — buy/sell buttons, price/volume inputs, quick submit using existing trade API
- `portfolio-fund-flow`: Per-position and aggregated moneyflow display showing main force net flow direction and magnitude
- `portfolio-sector-concentration`: Sector/industry concentration computation (backend) and visualization (frontend) for position-level risk assessment

### Modified Capabilities
- `portfolio`: The `/api/v1/portfolio` endpoint SHALL populate the `sector_concentration` field (currently always null) with computed sector breakdown data

## Impact

- **Backend**: `backend/app/api/portfolio.py` — add sector concentration computation logic using `stock_concept_map` from `stock_pool.db`
- **Backend models**: `backend/app/models/account.py` — `PortfolioSummary.sector_concentration` type may need refinement from `Optional[dict]` to a typed model
- **Frontend**: `PortfolioPage.tsx` — inline trade panel, fund flow badges, sector chart; `portfolio-page.css` — new component styles
- **Frontend API**: `frontend/src/api/client.ts` — no changes needed (all endpoints exist)
- **Dependencies**: No new packages
