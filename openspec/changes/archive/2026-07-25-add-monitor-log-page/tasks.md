## 1. Routing & Navigation

- [x] 1.1 Add `/monitor` route in `App.tsx`, lazy-load `MonitorLogPage`
- [x] 1.2 Add "监控日志" nav entry in `TopNav.tsx` with `FileSearch` icon

## 2. Page Component

- [x] 2.1 Create `MonitorLogPage.tsx` with filter bar (symbol input, result dropdown, date range, refresh button), paginated table, and skeleton/empty/error states
- [x] 2.2 Create `monitor-log-page.css` with styles matching the cockpit theme using `agent-theme.css` tokens

## 3. Detail Expansion

- [x] 3.1 Add expandable row behavior — click row to fetch `GET /api/v1/monitor/logs/{id}` and render gate_details + trend_details JSONB
- [x] 3.2 Style the expanded detail panel and gate/trend check result breakdown

## 4. Build Verification

- [x] 4.1 Run `npm run build` to verify TypeScript compilation and Vite bundle
