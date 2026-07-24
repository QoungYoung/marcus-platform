## ADDED Requirements

### Requirement: List reflect sessions
The system SHALL provide an API endpoint that returns all past weekly reflect group chat sessions sorted by creation date descending.

#### Scenario: Successful list with sessions
- **WHEN** the client sends `GET /api/v1/panel/reflect/sessions`
- **AND** one or more `*-reflect.json` files exist in `memory/weekly-reflect-logs/`
- **THEN** the response SHALL contain a JSON array of session summaries, each with `id`, `start_date`, `end_date`, `stance`, `position_limit`, `created_at`, and `reason`
- **AND** the array SHALL be sorted by `created_at` descending

#### Scenario: Empty list when no sessions exist
- **WHEN** the client sends `GET /api/v1/panel/reflect/sessions`
- **AND** no `*-reflect.json` files exist in `memory/weekly-reflect-logs/`
- **THEN** the response SHALL contain an empty JSON array

### Requirement: Get single reflect session
The system SHALL provide an API endpoint that returns the full content of a single reflect session by its ID.

#### Scenario: Successful retrieval
- **WHEN** the client sends `GET /api/v1/panel/reflect/sessions/{session_id}`
- **AND** a corresponding `*-reflect.json` file exists
- **THEN** the response SHALL contain the full session data including the complete `report` field

#### Scenario: Session not found
- **WHEN** the client sends `GET /api/v1/panel/reflect/sessions/{session_id}`
- **AND** no corresponding file exists
- **THEN** the response SHALL return HTTP 404 with an error message

### Requirement: Session continuity in reflect stream
The system SHALL accept optional `session_id` and `history_messages` parameters in the reflect stream endpoint to support continuing a past discussion.

#### Scenario: New reflect session without history
- **WHEN** the client sends `POST /api/v1/panel/reflect/stream` with only `message`
- **THEN** the backend SHALL generate a new session and forward the request to pi-server as before

#### Scenario: Continued reflect session with history
- **WHEN** the client sends `POST /api/v1/panel/reflect/stream` with `message`, `session_id`, and `history_messages`
- **THEN** the backend SHALL forward all three parameters to pi-server
- **AND** the pi-server SHALL receive the `history_messages` as conversation prefix context
