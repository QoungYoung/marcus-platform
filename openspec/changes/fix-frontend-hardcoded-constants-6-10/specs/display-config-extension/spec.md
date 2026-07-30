## ADDED Requirements

### Requirement: Display-config returns exit signal labels
The `/golden-pit/display-config` endpoint SHALL return an `exit_labels` mapping that provides Chinese display names for all exit signal types.

#### Scenario: Frontend fetches exit labels
- **WHEN** frontend calls `GET /golden-pit/display-config`
- **THEN** the response includes `exit_labels` with keys `half_exit`, `full_exit`, `stop_profit`, `fallback_exit` and their Chinese label values

### Requirement: Display-config returns trend icons
The `/golden-pit/display-config` endpoint SHALL return a `trend_icons` mapping that provides display icon characters for each trend state.

#### Scenario: Frontend fetches trend icons
- **WHEN** frontend calls `GET /golden-pit/display-config`
- **THEN** the response includes `trend_icons` with keys `declining`, `bottoming`, `recovering` and their icon string values

### Requirement: Display-config returns trend colors
The `/golden-pit/display-config` endpoint SHALL return a `trend_colors` mapping that provides display colors for each trend state.

#### Scenario: Frontend fetches trend colors
- **WHEN** frontend calls `GET /golden-pit/display-config`
- **THEN** the response includes `trend_colors` with keys `declining`, `bottoming`, `recovering` and their hex color values

### Requirement: GoldenPitPage uses display-config for exit and trend mappings
The GoldenPitPage component SHALL read exit labels, trend icons, and trend colors from the display-config API rather than using locally hardcoded records.

#### Scenario: Exit signal displayed on index card
- **WHEN** an index has an `exit_signal` value
- **THEN** the displayed label comes from `displayConfig.exit_labels[exit_signal]`, falling back to the raw signal key if absent

#### Scenario: Trend icon displayed on index card
- **WHEN** an index has a `trend` value
- **THEN** the displayed icon comes from `displayConfig.trend_icons[trend]`, falling back to '' if absent

#### Scenario: Trend color used in display
- **WHEN** an index has a `trend` value
- **THEN** the color used for the trend indicator comes from `displayConfig.trend_colors[trend]`

### Requirement: Chart reference lines use per-index threshold data
The trend chart SHALL position horizontal reference lines at the minimum `pit_greed` and minimum `entry_greed` values from the indices in the status API response, rather than hardcoded y=0.35 and y=0.40.

#### Scenario: Chart renders with dynamic reference lines
- **WHEN** the trend chart renders with indices data from the status API
- **THEN** the golden pit reference line is drawn at `y = min(indices[i].pit_greed)` and the warning reference line at `y = min(indices[i].entry_greed)`

### Requirement: Chart YAxis domain adapts to data range
The trend chart YAxis domain SHALL be computed as `[min(min_pit_greed - 0.05, data_min), max(data_max + 0.05, 0.50)]` clamped to `[0.15, 0.95]`, ensuring the reference lines are always visible within the chart.

#### Scenario: Chart domain adapts to visible data
- **WHEN** chart data ranges from 0.30 to 0.55
- **THEN** YAxis domain bottom is at most `min_pit_greed - 0.05` and top is at least `max(data) + 0.05`, clamped within [0.15, 0.95]

### Requirement: PortfolioPage gauge upper bound uses per-index thresholds
The PortfolioPage golden pit gauge SHALL compute its upper bound (`safeCeil`) as `max(max_entry_greed + 0.10, 0.50)` where `max_entry_greed` is the maximum `entry_greed` across all indices from the status API.

#### Scenario: Gauge bar renders with data-derived bounds
- **WHEN** the golden pit signal panel renders
- **THEN** the gauge bar fill percentage and warn-line marker position are computed using `pitThreshold` and `safeCeil` derived from the API data

### Requirement: ResonanceBadge has no local fallback formula
The ResonanceBadge component SHALL NOT duplicate the resonance multiplier calculation. If `multiplier` is not provided, the component SHALL display nothing instead of computing a fallback value.

#### Scenario: Badge renders with backend multiplier
- **WHEN** the `resonance_multiplier` field is present in the API response
- **THEN** the badge displays the backend-computed multiplier value

#### Scenario: Badge silent when multiplier absent
- **WHEN** the `resonance_multiplier` field is missing or null
- **THEN** the badge renders nothing (or a minimal placeholder)
