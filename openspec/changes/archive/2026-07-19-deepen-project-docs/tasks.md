## 1. Deep-dive Code Exploration

- [x] 1.1 Explore all 16 API route files to extract key endpoint paths, methods, and core function names
- [x] 1.2 Explore all 14 service files to extract class names, key methods, and polling intervals
- [x] 1.3 Explore all 12 model files to map Pydantic models and SQLAlchemy ORM classes
- [x] 1.4 Explore frontend src/ to map route-to-page-to-component relationships and Zustand stores
- [x] 1.5 Explore database schema by reading SQLAlchemy models and SQLite table definitions

## 2. Rewrite openspec/project.md

- [x] 2.1 Write Quick Navigation Index — 30+ keyword-to-filepath mappings grouped by business domain
- [x] 2.2 Write Core Data Flow diagrams — 3 ASCII diagrams (trade request, market data, background monitor)
- [x] 2.3 Write Module Quick Reference — every API/Service/Model file with key classes and methods
- [x] 2.4 Write Database Schema — PostgreSQL and SQLite tables with model names and key fields
- [x] 2.5 Write Frontend Component Tree — route→page→component hierarchy and Zustand store map

## 3. Create openspec/docs/code-patterns.md

- [x] 3.1 Write API endpoint template — FastAPI router + Pydantic model + service call + error handling
- [x] 3.2 Write background service template — daemon thread + polling loop + executor integration
- [x] 3.3 Write frontend page template — React component + Zustand store + API client call

## 4. Verification

- [x] 4.1 Verify all file paths in project.md point to existing files
- [x] 4.2 Verify code-patterns.md templates are syntactically consistent with project conventions
- [x] 4.3 Read the final project.md to ensure all 5 sections are coherent and complete
