## Purpose

Market data API — real-time quotes, K-line data, sector performance, money flow, technical indicators, and global market indices for the A-share market.

## Requirements

### Requirement: Market Indices
The system SHALL return current values of major A-share indices.

#### Scenario: Get index data
- **WHEN** GET /api/v1/market/indices is called
- **THEN** response includes SH, SZ, CYB (ChiNext), and other major indices with current value and change percentage

### Requirement: Stock Quotes
The system SHALL return real-time quote data for individual stocks.

#### Scenario: Get stock quote
- **WHEN** GET /api/v1/market/quote?symbol=<symbol> is called
- **THEN** response includes current price, change, volume, and basic fundamentals

### Requirement: K-line Data
The system SHALL return historical K-line (candlestick) data for technical analysis.

#### Scenario: Get daily K-line
- **WHEN** GET /api/v1/market/kline?symbol=<symbol>&period=daily is called
- **THEN** response includes OHLCV data points for the requested period

### Requirement: Money Flow
The system SHALL return capital flow data for stocks and sectors.

#### Scenario: Get money flow
- **WHEN** GET /api/v1/market/moneyflow?symbol=<symbol> is called
- **THEN** response includes main_net_inflow, super_large_net_inflow, and flow ratios

### Requirement: Sector Data
The system SHALL return sector/industry performance data.

#### Scenario: Get sector performance
- **WHEN** GET /api/v1/market/sectors is called
- **THEN** response includes sector name, change percentage, and leading stocks

### Requirement: Technical Indicators
The system SHALL compute and return technical indicators (Fibonacci, daily channel, entry filters).

#### Scenario: Calculate Fibonacci levels
- **WHEN** POST /api/v1/market/indicator/fibonacci is called with high/low prices
- **THEN** response includes retracement and extension levels

#### Scenario: Entry filter check
- **WHEN** POST /api/v1/market/indicator/entry-check is called with symbol
- **THEN** response includes pass/fail status for each entry filter rule

### Requirement: Global Market
The system SHALL return global market overview data.

#### Scenario: Get global overview
- **WHEN** GET /api/v1/market/global is called
- **THEN** response includes major global indices (S&P 500, NASDAQ, etc.), commodities, and forex rates
