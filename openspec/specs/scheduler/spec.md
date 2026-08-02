## Purpose

Task scheduler with CRUD operations — manage recurring market scans, scheduled reports, and automated trading operations via APScheduler.

## Requirements

### Requirement: Schedule CRUD
The system SHALL support creating, reading, updating, and deleting scheduled tasks.

#### Scenario: Create schedule
- **WHEN** POST /api/v1/scheduler with name, cron_expression, task_type is called
- **THEN** a new scheduled task is created and registered with APScheduler

#### Scenario: List schedules
- **WHEN** GET /api/v1/scheduler is called
- **THEN** all scheduled tasks are returned with their status (running/paused/error)

#### Scenario: Update schedule
- **WHEN** PUT /api/v1/scheduler/{id} is called with new parameters
- **THEN** the schedule is updated and APScheduler job is modified

#### Scenario: Delete schedule
- **WHEN** DELETE /api/v1/scheduler/{id} is called
- **THEN** the schedule is removed from APScheduler and database

### Requirement: Scheduler Status
The system SHALL report the overall scheduler health status.

#### Scenario: Health check
- **WHEN** GET /api/v1/health is called
- **THEN** response includes scheduler_status with running jobs count

### Requirement: Stop-Loss Monitor Control
The system SHALL allow enabling/disabling the stop-loss monitor via the scheduler API.

#### Scenario: Toggle stop-loss
- **WHEN** POST /api/v1/scheduler/stop-loss/toggle is called
- **THEN** stop-loss monitor is started or stopped accordingly

### Requirement: Task Types
The system SHALL support multiple task types including market scan, strategy execution, and custom tasks.

#### Scenario: Market scan task
- **WHEN** a scheduled market scan task fires
- **THEN** scan results are saved to database and available via scan API
