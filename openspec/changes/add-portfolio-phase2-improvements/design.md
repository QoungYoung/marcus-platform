## Context

Phase 1 added read-only performance analytics. Phase 2 adds actionability (inline trading) and risk context (fund flow, sector concentration). Two of three features require backend changes — the first backend work in this feature sequence.

Current state:
- `POST /api/v1/trades` accepts `{ symbol, side, price, volume, reason? }` — already wired to the VNPy paper engine
- `GET /api/v1/market/moneyflow/{symbol}` returns per-stock moneyflow with main/large/medium/small net flow breakdown, sourced from push2.eastmoney.com (real-time) with Tushare fallback
- `stock_pool.db` has a `stock_concept_map` table mapping stock codes to concept sectors
- `PortfolioSummary.sector_concentration` is `Optional[dict]` — defined in the model but never populated in the API handler

## Goals / Non-Goals

**Goals:**
- Enable quick buy/sell from the portfolio page without navigating to the trade page
- Show per-position moneyflow direction (主力 net inflow/outflow) as colored badges
- Compute and display sector concentration from existing stock-concept mapping data
- Keep the inline trade panel minimal — quick actions only, not a full trading interface

**Non-Goals:**
- No order confirmation/preview modal (use browser `confirm()` for simplicity)
- No real-time moneyflow polling (fetch once on page load)
- No sector reclassification or new data ingestion (use existing `stock_concept_map`)
- No changes to the stop-loss or position tier monitors

## Decisions

### D1: Inline trade panel — expand-on-click row pattern

Each position row gets a hover-visible "+" / "−" button pair. Clicking either expands an inline panel directly below the row with: direction indicator (buy/sell), price input (pre-filled with current price), volume input (shares), reason textarea (optional), and a submit button.

**Rationale:** A modal would obscure the positions table. A sidebar panel would require layout restructuring. Inline expansion keeps context — the user sees the position they're trading against while filling in the order.

**Alternative considered:** FAB menu with "quick buy" / "quick sell" that opens a modal. Rejected — loses position context and requires selecting the symbol separately.

### D2: Fund flow — badge on position row, summary strip above table

Each position row gets a small colored badge showing主力净流向: "流入" (green) / "流出" (red) / "平衡" (dim) based on `main_net` sign and magnitude (threshold: ±10% of market_value for significance). A summary strip above the positions table shows aggregated stats: "X只流入 / Y只流出".

Moneyflow data is fetched in parallel for all position symbols on page load. Use `Promise.allSettled` to avoid one failure blocking others.

**Data source:** `GET /api/v1/market/moneyflow/{symbol}` — returns `ThsMoneyflowResponse` with `main_net` (主力净流入, 元).

### D3: Sector concentration — backend computation + frontend donut

**Backend:** In the portfolio API handler, after computing positions, query `stock_concept_map` from `stock_pool.db` to map each position symbol → concept sectors. Aggregate position market_value by sector. Compute:
- `sectors`: `[{ name, weight_pct, stock_count }]` sorted by weight descending
- `max_sector`: name and weight of the largest sector
- `concentration_level`: "分散" (<30%), "适中" (30-50%), "集中" (>50%)

Return this as `sector_concentration` in the `PortfolioSummary` response.

**Frontend:** Add a sector donut chart (reusing the existing PieChart pattern) in the analysis section group. If sector data is unavailable (old API), show "暂无板块数据".

### D4: Moneyflow fetch strategy

Fetch moneyflow for all positions in a single `useEffect` on mount, after positions are loaded. Use `Promise.allSettled` with per-symbol calls to `marketApi.getMoneyflow(symbol)`. Cache results in a `Map<string, ThsMoneyflowResponse>`. Show loading skeleton while fetching.

**Risk:** N parallel API calls for N positions could be heavy. Mitigation: the portfolio typically has ≤10 positions, and each call is lightweight (single symbol lookup). If position count grows, add batching in a future iteration.

## Risks / Trade-offs

- **Sector mapping granularity** → `stock_concept_map` maps to concept sectors (e.g., "人工智能", "新能源"), not standard industry classifications (申万一级). This is finer-grained and more volatile than traditional sector classification. Trade-off: more actionable for A-share traders but less stable for long-term analysis. → Accept this — the platform already uses concept-based sector analysis everywhere.
- **Inline trade panel clutter** → Expanding a row pushes other rows down, which could be jarring. → Use `Animated` CSS transition on the expanded panel height.
- **Moneyflow data freshness** → Fetch once on page load; during trading hours, data becomes stale. → Accept for now; show a "刷新资金流" button. Real-time polling deferred.
- **Sector concentration accuracy** → A stock can belong to multiple concepts. Assigning market_value to each concept would double-count. → Assign each stock's market_value proportionally across its concepts (equal weight per concept per stock).
