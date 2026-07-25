## 1. Database Model

- [x] 1.1 Create `PositionAddMonitorLog` SQLAlchemy model in `backend/app/models/position_add_log.py`
- [x] 1.2 Register model import in `database.py` `init_db()` function

## 2. Monitor Integration

- [x] 2.1 Add `_write_monitor_log()` method to `PositionTierMonitor` that writes one row per position per check
- [x] 2.2 Call `_write_monitor_log()` from `_check_all_positions()` for each position after tier evaluation + gate arbitration
- [x] 2.3 Build `gate_details` JSONB from `GateResult.checks` for BLOCKED/EXECUTED results
- [x] 2.4 Build `trend_details` JSONB from `check_trend_strength()` result

## 3. API Endpoints

- [x] 3.1 Create `backend/app/api/monitor_log.py` with FastAPI router, tag `Monitor Log`
- [x] 3.2 Implement `GET /api/v1/monitor/logs` — paginated list with filters (symbol, result, date_from, date_to), sorted by timestamp DESC
- [x] 3.3 Implement `GET /api/v1/monitor/logs/{log_id}` — single row detail with full gate_details and trend_details
- [x] 3.4 Register router in `main.py`

## 4. Verification

- [x] 4.1 Start backend, verify `position_add_monitor_log` table is auto-created
- [ ] 4.2 Wait for a monitoring cycle, verify rows are written via API query
