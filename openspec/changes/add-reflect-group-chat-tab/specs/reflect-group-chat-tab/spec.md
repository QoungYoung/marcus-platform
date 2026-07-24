## ADDED Requirements

### Requirement: Tab switcher in session panel
The ChatContainer session panel SHALL display a tab switcher with "Chat" and "Group Chat" tabs to filter sessions by type.

#### Scenario: User switches to Group Chat tab
- **WHEN** the user clicks the "Group Chat" tab
- **THEN** the session list SHALL display only reflect-type sessions grouped by ISO week
- **AND** the "Chat" tab SHALL appear inactive

#### Scenario: User switches back to Chat tab
- **WHEN** the user clicks the "Chat" tab
- **THEN** the session list SHALL display only chat-type sessions (existing behavior)
- **AND** the "Group Chat" tab SHALL appear inactive

#### Scenario: Default tab on page load
- **WHEN** the page loads and the last used mode was "reflect"
- **THEN** the "Group Chat" tab SHALL be active by default
- **AND** reflect sessions SHALL be displayed

### Requirement: Week-grouped group chat list
The Group Chat tab SHALL merge backend reflect sessions (from API) with frontend IndexedDB reflect sessions, deduplicate them, and display them grouped by ISO week.

#### Scenario: Sessions from both sources exist
- **WHEN** the Group Chat tab is active
- **AND** backend API returns 2 reflect sessions
- **AND** IndexedDB contains 1 reflect session
- **THEN** all 3 sessions SHALL be merged into the list
- **AND** sessions SHALL be grouped under week headers (e.g., "2026年第31周 (7/21-7/25)")

#### Scenario: Only IndexedDB sessions exist
- **WHEN** the Group Chat tab is active
- **AND** backend API returns an empty list
- **THEN** only IndexedDB reflect sessions SHALL be displayed

#### Scenario: No reflect sessions at all
- **WHEN** the Group Chat tab is active
- **AND** both backend API and IndexedDB have no reflect sessions
- **THEN** an empty state message SHALL be displayed

#### Scenario: Loading state
- **WHEN** the Group Chat tab becomes active
- **AND** the backend API request is in flight
- **THEN** a loading indicator SHALL be shown until the request completes

### Requirement: View and resume a past group chat
The user SHALL be able to click a past group chat session to view its full discussion and send new messages to continue the conversation.

#### Scenario: Resume an IndexedDB reflect session
- **WHEN** the user clicks a group chat that exists in IndexedDB
- **THEN** the full message history SHALL be loaded into the ChatPanel
- **AND** the mode SHALL switch to "reflect"
- **AND** the user SHALL be able to send new messages that continue the discussion via `POST /panel/reflect/stream` with the same `session_id`

#### Scenario: View a backend-only reflect session
- **WHEN** the user clicks a group chat that exists only in backend files
- **THEN** the report content SHALL be fetched via `GET /panel/reflect/sessions/{id}`
- **AND** the report SHALL be converted to chat messages and rendered in the ChatPanel
- **AND** a new IndexedDB session SHALL be created to store these messages
- **AND** the user SHALL be able to continue the discussion

#### Scenario: Continue discussion with history context
- **WHEN** the user sends a new message in a resumed group chat
- **THEN** the request SHALL include `session_id` and `history_messages`
- **AND** `history_messages` SHALL contain the most recent 50 messages from the session

### Requirement: New group chat creation
The Group Chat tab SHALL provide a button to create a new reflect group chat session.

#### Scenario: Create new group chat
- **WHEN** the user clicks "New Group Chat" in the Group Chat tab
- **THEN** a new session SHALL be created with a unique UUID
- **AND** the mode SHALL switch to "reflect"
- **AND** the ChatPanel SHALL be ready for the user to enter a discussion topic
- **AND** the new session SHALL have `type: 'reflect'` in its metadata

### Requirement: Session type metadata
The `SessionMeta` interface SHALL include a `type` field to distinguish chat sessions from reflect sessions.

#### Scenario: New chat session
- **WHEN** a new session is created in "chat" mode
- **THEN** the session metadata SHALL have `type: 'chat'`

#### Scenario: New reflect session
- **WHEN** a new session is created in "reflect" mode
- **THEN** the session metadata SHALL have `type: 'reflect'`

#### Scenario: Legacy session without type
- **WHEN** an existing session has no `type` field in its metadata
- **THEN** it SHALL be treated as `type: 'chat'`
