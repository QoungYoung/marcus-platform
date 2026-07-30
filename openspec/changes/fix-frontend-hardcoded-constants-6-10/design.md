## Context

The previous change `fix-frontend-hardcoded-constants` established the pattern: the backend's `GET /golden-pit/display-config` endpoint is the single source of truth for display mappings (status_colors, status_labels, strategy_labels). The frontend fetches this once and caches it. This change extends that pattern to three additional display mappings (exit_labels, trend_icons, trend_colors) and replaces two remaining hardcoded formulas (chart domain/reference lines, gauge upper bound).

## Goals / Non-Goals

**Goals:**
- Extend `_display_config()` to include `exit_labels`, `trend_icons`, `trend_colors`
- Remove local `EXIT_LABELS`, `TREND_ICONS`, `TREND_COLORS` from GoldenPitPage.tsx
- Replace chart YAxis domain `[0.2, 0.9]` with dynamic bounds based on data range plus threshold offsets
- Replace chart ReferenceLine y-positions with actual `pit_greed` / `entry_greed` values from the API
- Replace `safeCeil` formula in PortfolioPage with a backend-computed or data-derived value
- Remove ResonanceBadge fallback formula; display nothing when backend value absent

**Non-Goals:**
- Changing the exit signal determination logic in the backend
- Modifying the trend detection algorithm
- Adding new API endpoints (extends existing `/display-config`)

## Decisions

### Decision 1: Extend existing display-config, not create new endpoints

**Choice:** Add `exit_labels`, `trend_icons`, `trend_colors` to the existing `_display_config()` dict.

**Why:** Follows the established pattern. One API call fetches all display metadata. Avoids endpoint proliferation for what are fundamentally the same kind of data (display labels/colors).

### Decision 2: Chart domain computed from data, not a new API field

**Choice:** For the trend chart, compute YAxis domain as `[min(pit_greed) - 0.05, max(data_greed) + 0.05]` clamped to `[0.15, 0.95]`, and position reference lines at the minimum `pit_greed` and minimum `entry_greed` from the indices data.

**Why:** The trend chart uses per-index data, so the domain should adapt to the actual data range. The per-index thresholds (`pit_greed`, `entry_greed`) are already in the status API response. The reference line positions dynamically match the actual thresholds. No backend change needed for this.

**Alternative considered:** Adding chart config to display-config API. Rejected — chart domain is data-dependent, not a static config. Each index has different thresholds, and the chart shows multiple indices simultaneously.

### Decision 3: Gauge upper bound from index data, not a hardcoded formula

**Choice:** Compute `safeCeil` as `max(max_entry_greed + 0.10, 0.50)` where `max_entry_greed` is the maximum `entry_greed` across all indices from the API. This is done in the frontend without a new backend field.

**Why:** The formula uses values already in the API response. The `+0.10` margin provides visual breathing room above the highest warning threshold. The `0.50` floor ensures the gauge remains meaningful even when all entry_greed values cluster low.

### Decision 4: Remove ResonanceBadge fallback formula

**Choice:** Delete the inline `pitCount >= 4 ? 1.3 : ...` fallback. If `resonance_multiplier` is not present, display nothing.

**Why:** The backend always returns `resonance_multiplier` in the window status. A fallback that duplicates backend logic can produce inconsistent values if the backend formula changes. Removing it makes any backend API issue visible (missing value) rather than silently producing potentially wrong results.

## Risks / Trade-offs

- **Chart domain adapts to data**: If only indices with very high greed are displayed, the domain could compress the reference lines. → Mitigation: clamp domain to `[0.15, 0.95]` to ensure reference lines are always visible
- **Display-config grows**: As more mappings are added, the single endpoint could become a dumping ground. → Mitigation: only add mappings that are (a) multiple key-value pairs, (b) shared across components, (c) defined by the backend
