## Why

The previous change (`fix-frontend-hardcoded-constants`) eliminated 5 groups of hardcoded display constants. However, the same audit revealed additional hardcoded values that remain — chart domain bounds, exit labels, trend icon mappings, and gauge formulas. When backend thresholds or labels change, these frontend constants silently diverge, causing incorrect visual indicators and misleading labels.

## What Changes

- **PortfolioPage gauge upper bound**: Replace hardcoded `safeCeil = max(warnLine + 0.10, 0.50)` formula with a value computed from the backend's per-index `pit_greed` / `entry_greed` thresholds
- **GoldenPitPage trend chart**: Replace hardcoded YAxis domain `[0.2, 0.9]` and ReferenceLine y-positions (`0.35`, `0.40`) with values derived from the actual per-index thresholds returned by the API
- **Display-config API extension**: Add `exit_labels`, `trend_icons`, `trend_colors` to the existing `/golden-pit/display-config` endpoint, following the established pattern
- **GoldenPitPage exit/trend displays**: Remove local `EXIT_LABELS`, `TREND_ICONS`, `TREND_COLORS` records and consume them from the display-config API
- **ResonanceBadge fallback**: Remove the duplicate multiplier calculation formula; display nothing when the backend value is absent instead of guessing

## Capabilities

### New Capabilities
- `display-config-extension`: Extend the existing display-config endpoint with exit_labels, trend_icons, and trend_colors mappings

### Modified Capabilities
- `golden-pit-chart`: Chart YAxis domain and reference line positions now adapt to per-index threshold data from the API

## Impact

- **Backend**: `golden_pit_service.py` — extend `_display_config()` to include `exit_labels`, `trend_icons`, `trend_colors`
- **Frontend**: `GoldenPitPage.tsx` — remove local EXIT_LABELS/TREND_ICONS/TREND_COLORS, use display-config instead; replace hardcoded chart bounds; remove resonance fallback formula
- **Frontend**: `PortfolioPage.tsx` — replace `safeCeil` formula with backend-derived gauge upper bound
