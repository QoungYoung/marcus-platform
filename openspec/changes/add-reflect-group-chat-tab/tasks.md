## 1. Backend API — Reflect Session History

- [x] 1.1 Add `GET /panel/reflect/sessions` endpoint: scan `memory/weekly-reflect-logs/` for `*-reflect.json` files, parse each, return sorted list with `id`, `start_date`, `end_date`, `stance`, `position_limit`, `created_at`, `reason`
- [x] 1.2 Add `GET /panel/reflect/sessions/{session_id}` endpoint: read a single reflect JSON file by filename-derived ID, return full record including `report`
- [x] 1.3 Modify `POST /panel/reflect/stream` request body: add optional `session_id` (str) and `history_messages` (list) fields; forward both to pi-server in the proxied payload

## 2. Frontend API Client

- [x] 2.1 Add `reflectApi` to `client.ts` with `getSessions()` and `getSession(id)` methods calling the new backend endpoints

## 3. Frontend — Session Type Metadata

- [x] 3.1 Add `type: 'chat' | 'reflect'` field to `SessionMeta` interface
- [x] 3.2 Update `updateSessionMeta` calls: set `type` based on active mode when saving a session (chat → `'chat'`, reflect → `'reflect'`)
- [x] 3.3 Add backward compatibility: treat sessions with no `type` field as `'chat'` in `buildSessionsList()`

## 4. Frontend — Tab Switcher UI

- [x] 4.1 Replace the "会话" header with a two-tab switcher: `[💬 聊天]` and `[👥 群聊]`
- [x] 4.2 Add `tabView` state (`'chat' | 'group_chat'`), defaulting from `localStorage.getItem('marcus_chat_mode')`
- [x] 4.3 Persist the active tab choice to localStorage on tab switch
- [x] 4.4 Style the active/inactive tab states to match existing gold/purple theme

## 5. Frontend — Group Chat Tab Content

- [x] 5.1 Add "New Group Chat" button at the top of the group chat tab, calling the existing reflect new-session logic with `type: 'reflect'`
- [x] 5.2 Fetch backend reflect sessions via `reflectApi.getSessions()` when group chat tab mounts
- [x] 5.3 Merge backend sessions with IndexedDB reflect sessions (filter `sessionsList` by `type === 'reflect'`)
- [x] 5.4 Group merged sessions by ISO week number, derive date range for week headers
- [x] 5.5 Render week-grouped list with headers like `📅 2026年第31周 (7/21-7/25)`
- [x] 5.6 Each session card shows: title (or date range), stance badge (green/yellow/red dot), message count, timestamp
- [x] 5.7 Handle empty state: show placeholder when no reflect sessions exist
- [x] 5.8 Handle loading state: show spinner/skeleton while backend API is in flight

## 6. Frontend — View & Resume Group Chat

- [x] 6.1 Click on an IndexedDB reflect session: load full messages via `loadSession(id)` into ChatPanel, switch mode to `'reflect'`
- [x] 6.2 Click on a backend-only reflect session: fetch report via `reflectApi.getSession(id)`, convert report markdown to a single assistant message, create a new IndexedDB session, render in ChatPanel
- [x] 6.3 Implement "continue discussion": when user sends a message in a resumed group chat, pass `session_id` and last 50 `history_messages` to `POST /panel/reflect/stream`
- [x] 6.4 Generate unique UUID for every new reflect session (replace hardcoded `"frontend_panel_stream"`)
- [x] 6.5 Update reflect stream response handler to save messages under the correct session ID

## 7. Polish & Edge Cases

- [x] 7.1 Deduplicate merged sessions: if a backend file and IndexedDB session cover the same date range, show only one entry (prefer IndexedDB for richer message history)
- [x] 7.2 Ensure the existing mode toggle button and the new tab switcher stay in sync (switching via either updates both)
- [x] 7.3 Test: create new group chat → verify it appears in group chat tab with correct week grouping
- [x] 7.4 Test: resume a past group chat → send message → verify it continues the same session
- [x] 7.5 Test: empty reflect history (no backend files, no IndexedDB sessions) → empty state displays correctly
