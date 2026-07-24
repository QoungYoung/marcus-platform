## Why

The ChatContainer currently mixes regular chat and expert panel reflect sessions in a single undifferentiated session list. Weekly reflect group chats are auto-triggered by the scheduler and saved to backend files, but there is no way to browse them, resume past discussions, or see which sessions are group chats vs regular chats. This makes the reflect feature feel disconnected from the main UI.

## What Changes

- Add a tab switcher in the left session panel to toggle between **Chat** (regular sessions) and **Group Chat** (reflect sessions)
- Backend API to list and read historical weekly reflect sessions from `memory/weekly-reflect-logs/`
- Merge backend file-based reflect sessions with frontend IndexedDB reflect sessions into a unified, week-grouped list
- Allow resuming a past group chat: load its messages and continue the discussion via the same pi-server session
- New group chat creation reuses the existing reflect mode flow with a unique session ID
- **BREAKING**: `POST /panel/reflect/stream` now accepts optional `session_id` and `history_messages` for session continuity

## Capabilities

### New Capabilities

- `reflect-session-history`: Backend API to list and retrieve past weekly reflect group chat sessions from `memory/weekly-reflect-logs/`
- `reflect-group-chat-tab`: Frontend tab in ChatContainer left panel to browse, resume, and create reflect group chat sessions, merging backend history with IndexedDB sessions

### Modified Capabilities

<!-- No existing spec-level requirements are changing -->

## Impact

| Area | Change |
|------|--------|
| `backend/app/api/panel.py` | New `GET /panel/reflect/sessions` and `GET /panel/reflect/sessions/{id}` endpoints; modify `POST /panel/reflect/stream` to accept session continuity params |
| `frontend/src/components/ChatContainer.tsx` | Tab UI, dual datasource merge, session grouping by week, type-aware session metadata |
| `frontend/src/api/client.ts` | New API client functions for reflect session endpoints |
| `memory/weekly-reflect-logs/` | Read by new backend API; no schema change to existing files |
| Pi Server | Requires session persistence by session_id (no change if already supported; fallback: pass history as context) |
