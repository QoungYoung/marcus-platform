## Purpose

Comprehensive project documentation enabling AI coding assistants to navigate and understand the marcus-platform codebase without reading source files. Covers quick navigation, data flows, module reference, database schema, frontend architecture, and code patterns.

## Requirements

### Requirement: Quick Navigation Index
The `openspec/project.md` SHALL include a quick navigation table mapping 30+ business keywords to exact file paths, enabling Claude to locate relevant code without reading source files.

#### Scenario: Bug fix navigation
- **WHEN** Claude searches for "stop loss" related code
- **THEN** the navigation table SHALL point to `backend/app/services/stop_loss_monitor.py` and `backend/app/core/trading/marcus_trade.py`

#### Scenario: Feature addition navigation
- **WHEN** Claude needs to add a new API endpoint
- **THEN** the navigation table SHALL point to the relevant API file in `backend/app/api/` and the model file in `backend/app/models/`

### Requirement: Core Data Flow Diagrams
The `openspec/project.md` SHALL contain ASCII diagrams showing how data flows through the system for three critical paths: trade request flow, market data flow, and background monitor flow.

#### Scenario: Understanding trade execution flow
- **WHEN** Claude reads the data flow section
- **THEN** the diagram SHALL show the complete path from frontend POST → API router → Executor → Database → Response

#### Scenario: Understanding monitor lifecycle
- **WHEN** Claude reads the background monitor flow
- **THEN** the diagram SHALL show monitor startup in main.py lifespan → polling loop → condition check → trade execution → notification

### Requirement: Module Quick Reference
The `openspec/project.md` SHALL list every API, Service, and Model file with its key classes and core methods, organized by category.

#### Scenario: Finding a service's methods
- **WHEN** Claude needs to understand what a service does
- **THEN** the module reference SHALL show the key classes and their public methods for that service file

### Requirement: Database Schema Documentation
The `openspec/project.md` SHALL document all database tables across PostgreSQL and SQLite, including model class names, table names, and primary fields.

#### Scenario: Finding where trade data is persisted
- **WHEN** Claude needs to query trade records
- **THEN** the schema section SHALL identify that trades are in SQLite `trades.db` and backtest results in PostgreSQL `backtest_trades` table

### Requirement: Frontend Component Tree
The `openspec/project.md` SHALL document the React component hierarchy including route-to-page mapping, shared components, and Zustand store structure.

#### Scenario: Finding where to modify a UI
- **WHEN** Claude needs to modify the portfolio page
- **THEN** the frontend section SHALL identify `src/pages/PortfolioPage.tsx` and its relevant store

### Requirement: Code Patterns Reference
The `openspec/docs/code-patterns.md` SHALL provide copyable templates for common development tasks including: adding a new API endpoint, adding a new background service, and adding a new frontend page.

#### Scenario: Adding a new API endpoint
- **WHEN** a developer needs to add a new API route
- **THEN** the patterns document SHALL provide a template showing the FastAPI router decorator, Pydantic model, dependency injection, and service call pattern

#### Scenario: Adding a new background monitor
- **WHEN** a developer needs to add a new monitoring thread
- **THEN** the patterns document SHALL provide a template showing daemon thread setup, polling loop, and executor integration
