## ADDED Requirements

### Requirement: Sector Concentration Backend Computation
The system SHALL populate the `sector_concentration` field in the portfolio summary API response with computed sector breakdown data.

#### Scenario: Positions with sector mapping
- **WHEN** `GET /api/v1/portfolio` is called and positions exist with known concept sector mappings in `stock_concept_map`
- **THEN** the response includes `sector_concentration` containing: `sectors` array (each with `name`, `weight_pct`, `stock_count`), `max_sector` (name and weight of largest), and `concentration_level` ("分散" / "适中" / "集中")

#### Scenario: Positions without sector mapping
- **WHEN** a position's symbol is not found in `stock_concept_map`
- **THEN** that position's market value is categorized under an "其他/未分类" sector

#### Scenario: No positions
- **WHEN** the account has no open positions
- **THEN** `sector_concentration` is null

### Requirement: Sector Concentration Frontend Display
The system SHALL render a sector concentration visualization on the portfolio page using the data from the `sector_concentration` API field.

#### Scenario: Sector data available
- **WHEN** the portfolio summary includes `sector_concentration` with sector data
- **THEN** the system renders a donut or horizontal bar chart showing sector weight distribution, labeled with sector names and percentages

#### Scenario: Sector data unavailable
- **WHEN** `sector_concentration` is null or the API returns an older response without the field
- **THEN** the system displays "暂无板块数据" placeholder with dim styling

#### Scenario: High concentration warning
- **WHEN** the largest sector exceeds 50% weight
- **THEN** the concentration chart includes a visual warning indicator (amber/red accent on the dominant sector)
