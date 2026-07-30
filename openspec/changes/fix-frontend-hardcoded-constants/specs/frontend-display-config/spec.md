## ADDED Requirements

### Requirement: Display config endpoint
The system SHALL provide a `GET /api/v1/golden-pit/display-config` endpoint that returns frontend display metadata.

#### Scenario: Fetch display colors
- **WHEN** frontend calls the display-config endpoint
- **THEN** response includes `status_colors` with hex values matching backend `STATUS_MAP`

#### Scenario: Fetch strategy labels
- **WHEN** frontend calls the display-config endpoint
- **THEN** response includes `strategy_labels` mapping all 9 DCA strategy codes to Chinese labels

#### Scenario: Cached by frontend
- **WHEN** frontend loads the page
- **THEN** it fetches display-config once and caches the result for the session

### Requirement: DCA strategy name translation
The backend SHALL provide a `_strategy_label()` function that maps all DCA strategy codes to human-readable Chinese labels.

#### Scenario: Translate lump_entry
- **WHEN** strategy is "lump_entry"
- **THEN** returns "一次性建仓"

#### Scenario: Translate uniform variants
- **WHEN** strategy is "uniform_3", "uniform_5", "uniform_7", "uniform_10", or "uniform_15"
- **THEN** returns "3日等权", "5日等权", "7日等权", "10日等权", or "15日等权" respectively

#### Scenario: Translate other strategies
- **WHEN** strategy is "front_loaded", "back_loaded", or "triangle"
- **THEN** returns "前重后轻", "前轻后重", or "三角加权" respectively

### Requirement: Per-index greed thresholds in GoldenPitPage chart
The GoldenPitPage greed trend chart SHALL use per-index thresholds or generic labels instead of hardcoded 0.35/0.40.

#### Scenario: Reference lines with generic labels
- **WHEN** chart renders with multiple indices
- **THEN** reference line labels say "参考线 (0.35)" and "参考线 (0.40)" instead of "黄金坑线" and "预警线"

### Requirement: Per-index greed thresholds in PortfolioPage
PortfolioPage SHALL use per-index `pit_greed` and `entry_greed` from the API response instead of fixed 0.35/0.40 thresholds.

#### Scenario: Determine panic status from API data
- **WHEN** PortfolioPage determines greed level
- **THEN** it uses the minimum `pit_greed` and minimum `entry_greed` from all held indices as the classification thresholds

### Requirement: Status color consistency
Frontend status colors SHALL match backend `STATUS_MAP` exactly.

#### Scenario: Colors from display-config
- **WHEN** frontend renders index status
- **THEN** it uses colors from the `display-config` endpoint, not locally defined hex values
