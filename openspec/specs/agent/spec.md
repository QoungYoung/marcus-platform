## Purpose

AI Trading Agent — conversational interface powered by DeepSeek for trade analysis, decision support, and expert panel group chat (reflect mode).

## Requirements

### Requirement: Chat Endpoint
The system SHALL provide a chat endpoint with streaming support via DeepSeek API.

#### Scenario: Send message
- **WHEN** POST /api/v1/agent/chat with message and session_id is called
- **THEN** DeepSeek responds with streaming or non-streaming reply based on request

#### Scenario: Stream response
- **WHEN** POST /api/v1/agent/chat with stream=true is called
- **THEN** response is streamed as SSE events with incremental content

### Requirement: Session Management
The system SHALL maintain conversation sessions with tree-based branching.

#### Scenario: Create session
- **WHEN** a new chat is started without session_id
- **THEN** a new session is created and returned with session_id

#### Scenario: Continue session
- **WHEN** chat is sent with existing session_id
- **THEN** conversation context is preserved and appended to

### Requirement: Context Compaction
The system SHALL automatically compact long conversations when token limits are approached.

#### Scenario: Auto-compact
- **WHEN** conversation exceeds token threshold
- **THEN** older messages are summarized and context is compacted transparently

### Requirement: Expert Panel (Reflect Mode)
The system SHALL support multi-role expert panel discussions proxied through Pi Server.

#### Scenario: Start panel discussion
- **WHEN** POST /api/v1/panel with topic and roles is called
- **THEN** Pi Server orchestrates multi-agent discussion with defined expert roles

#### Scenario: Panel roles
- **WHEN** panel is configured
- **THEN** available roles include: risk controller, trend trader, data analyst, devil's advocate, moderator

### Requirement: System Prompts
The system SHALL support configurable system prompts stored in PostgreSQL.

#### Scenario: Seed prompts
- **WHEN** application starts
- **THEN** default system prompts are seeded if not present (idempotent)

#### Scenario: Update prompt
- **WHEN** PUT /api/v1/prompts/{id} is called
- **THEN** prompt content is updated and used for subsequent chat requests
