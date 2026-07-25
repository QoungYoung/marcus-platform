## ADDED Requirements

### Requirement: Inline Quick Trade Panel
The system SHALL allow users to initiate buy or sell orders for a held position directly from the portfolio positions table without navigating to a separate trade page.

#### Scenario: Open buy panel
- **WHEN** user hovers over a position row and clicks the "+" button
- **THEN** an inline panel expands below that row with: direction pre-set to "买入", symbol pre-filled, price input defaulting to current price, volume input, optional reason textarea, and a submit button

#### Scenario: Open sell panel
- **WHEN** user hovers over a position row and clicks the "−" button
- **THEN** an inline panel expands below that row with: direction pre-set to "卖出", symbol pre-filled, price input defaulting to current price, volume input defaulting to current holding volume, optional reason textarea, and a submit button

#### Scenario: Submit trade
- **WHEN** user fills in price and volume and clicks submit
- **THEN** the system calls `POST /api/v1/trades` with the order details, shows a success confirmation, collapses the panel, and refreshes the portfolio summary

#### Scenario: Invalid input
- **WHEN** user submits with price ≤ 0, volume ≤ 0, or volume exceeding available shares (for sell)
- **THEN** the system shows an inline validation error and does not submit the order

#### Scenario: Trade API failure
- **WHEN** the trade API returns an error
- **THEN** the system displays the error message inline and keeps the panel open for the user to retry

### Requirement: Per-Position Fund Flow Badge
The system SHALL display a moneyflow direction badge on each position row showing the main force net flow for that stock.

#### Scenario: Main force net inflow
- **WHEN** the stock's `main_net` (主力净流入) > 0 and magnitude exceeds the significance threshold
- **THEN** the badge shows "主力流入" in green

#### Scenario: Main force net outflow
- **WHEN** the stock's `main_net` < 0 and magnitude exceeds the significance threshold
- **THEN** the badge shows "主力流出" in red

#### Scenario: Balanced flow
- **WHEN** the stock's `main_net` magnitude is below the significance threshold
- **THEN** the badge shows "平衡" in dim color

#### Scenario: Moneyflow data unavailable
- **WHEN** the moneyflow API call fails or returns no data for a stock
- **THEN** the badge shows "—" in dim color with no directional indicator

### Requirement: Fund Flow Summary Strip
The system SHALL display an aggregated fund flow summary above the positions table.

#### Scenario: Mixed flow
- **WHEN** some positions show inflow and others show outflow
- **THEN** the summary shows "X只流入 / Y只流出" with corresponding green/red counts

#### Scenario: All inflow
- **WHEN** all positions with available data show inflow
- **THEN** the summary shows "全部流入" in green

#### Scenario: All outflow
- **WHEN** all positions with available data show outflow
- **THEN** the summary shows "全部流出" in red
