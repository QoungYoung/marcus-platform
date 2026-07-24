## Context

The ChatContainer component (~3100 lines) has two modes: `chat` (1:1 AI conversation) and `reflect` (expert panel group chat proxied to pi-server). Currently:

- **Session panel** lists all sessions from IndexedDB without distinguishing chat vs reflect
- **Reflect sessions** from scheduled `weekly_reflect` tasks are saved as JSON/MD files in `memory/weekly-reflect-logs/` on the backend — invisible to the frontend
- **Manual reflect** sessions triggered via the UI toggle are saved to IndexedDB with title `'专家群聊'`
- **Session IDs** for reflect are hardcoded (`"frontend_panel_stream"`) rather than unique, preventing multi-session continuity
- The `SessionMeta` interface has no `type` field to distinguish chat from reflect

The pi-server handles the actual expert panel orchestration (5 experts, multi-round discussion). The backend `panel.py` is a thin proxy. Session persistence depends on pi-server's ability to retain context by session_id.

## Goals / Non-Goals

**Goals:**
- Add a tab switcher (`[💬 Chat] [👥 Group Chat]`) to the left session panel
- Chat tab: show only `type: 'chat'` sessions (existing behavior, filtered)
- Group Chat tab: merge backend file-based reflect history with IndexedDB reflect sessions, grouped by ISO week
- Click a past group chat to view its full discussion and continue the conversation
- "New Group Chat" button in the group chat tab starts a fresh reflect session
- Backend API to expose reflect history from `memory/weekly-reflect-logs/`
- Unique session IDs for all reflect sessions to enable continuity

**Non-Goals:**
- Changing the pi-server's expert panel orchestration logic
- Modifying the scheduler's weekly_reflect task behavior
- Real-time sync of active group chats across devices/browsers
- Deleting or editing historical reflect sessions from the backend
- Changing the reflect system prompt or expert configuration

## Decisions

### 1. Session type discrimination

**Decision**: Add a `type: 'chat' | 'reflect'` field to `SessionMeta`, stored in localStorage alongside existing metadata.

**Rationale**: Minimal change to existing storage. The type is set at session creation time based on the active mode. Existing sessions without a `type` field default to `'chat'` for backward compatibility.

**Alternative considered**: Separate IndexedDB stores for chat vs reflect. Rejected — overkill; the metadata field is sufficient for filtering.

### 2. Dual datasource merge strategy

**Decision**: Fetch backend reflect sessions via API, then merge with IndexedDB reflect sessions. Deduplicate by date range (backend files use `start_date_to_end_date` naming; IndexedDB sessions use timestamps within the same week).

```
Data sources:
  ┌──────────────────────────────┐
  │ GET /panel/reflect/sessions  │  ← Backend: memory/weekly-reflect-logs/*.json
  │ → [{id, start_date, end_date, stance, position_limit, created_at}]  │
  └──────────┬───────────────────┘
             │ merge by week
  ┌──────────┴───────────────────┐
  │ IndexedDB reflect sessions   │  ← Frontend: filter type='reflect'
  │ → [{id, title, messages, createdAt}]                           │
  └──────────┬───────────────────┘
             │
             ▼
  ┌──────────────────────────────┐
  │ Unified week-grouped list    │
  │ Week 31: [backend reflect,   │
  │           manual reflect, ...]│
  │ Week 30: [...]               │
  └──────────────────────────────┘
```

**Rationale**: Backend files and IndexedDB sessions represent the same logical entity (a reflect discussion) but from different triggers. Users want to see both in one place. Dedup uses week number + date proximity.

### 3. Session continuity

**Decision**: Generate unique UUID session IDs for every reflect session (replacing the hardcoded `"frontend_panel_stream"`). When resuming, pass `session_id` and `history_messages` to `POST /panel/reflect/stream`. The pi-server uses `session_id` to restore context.

**Fallback**: If pi-server does not support session persistence, the `history_messages` array provides full conversation context as a prefix to the new prompt. This guarantees continuity regardless of pi-server behavior.

**Note**: The pi-server endpoint is `/chat/stream`. Whether it maintains session state by `session_id` is an open question. The `history_messages` fallback removes this dependency.

### 4. Backend API design

**Decision**: Add two read-only GET endpoints to the existing `panel.py` router.

```
GET  /api/v1/panel/reflect/sessions
  → list all JSON files in memory/weekly-reflect-logs/
  → return sorted by created_at desc

GET  /api/v1/panel/reflect/sessions/{session_id}
  → read a single reflect JSON file
  → return full report content
```

**Rationale**: Keep it simple. These are read-only views over existing files. No new database tables needed. The `session_id` is derived from the filename: `{start_date}_to_{end_date}-reflect`.

**Modified POST endpoint**:

```
POST /api/v1/panel/reflect/stream
  Body: {
    message: str,
    session_id?: str,         // NEW: for session continuity
    history_messages?: [...]  // NEW: fallback context
  }
```

### 5. Week grouping

**Decision**: Use ISO week number for grouping. Parse dates from backend files (`start_date` field) and IndexedDB `createdAt` timestamps. Group header format: `📅 2026年第31周 (7/21-7/25)`.

**Rationale**: Chinese trading weeks are Mon-Fri, which aligns with ISO week. Displaying the actual date range makes it concrete.

### 6. Resume flow

```
User clicks a past group chat
  │
  ├─ Is it in IndexedDB? (type='reflect' session)
  │   ├─ YES → loadSession() → restore messages → user sends new message
  │   │         → POST /reflect/stream {message, session_id, history_messages}
  │   │
  │   └─ NO → it's a backend-only file
  │           → GET /sessions/{id} → convert report to message format
  │           → create new IndexedDB session with those messages
  │           → user sends new message → same flow as above
  │
  └─ In both cases: switch mode to 'reflect', load messages, focus input
```

## Risks / Trade-offs

- **[Risk] pi-server does not persist sessions by session_id** → Mitigation: `history_messages` fallback always included; even without server-side persistence, the conversation continues with full context.

- **[Risk] Backend reflect files may not exist (scheduler never ran)** → Mitigation: API returns empty list gracefully; group chat tab shows only IndexedDB sessions with an empty state message.

- **[Risk] Large history_messages payload may exceed pi-server limits** → Mitigation: Truncate to last 50 messages (approx. expert panel discussions are bounded in length).

- **[Trade-off] Deduplication by week is imperfect** → A manual reflect and a scheduled reflect in the same week may appear as two entries even if they cover the same topic. Acceptable — they have different session IDs and content.

- **[Trade-off] Tab state resets on page refresh** → The active tab defaults to the last used mode from localStorage (`marcus_chat_mode`). Acceptable for v1.

## Open Questions

1. **Does pi-server support session persistence by `session_id`?** If yes, we can skip the `history_messages` fallback and fully rely on server-side context. Needs a quick test.
2. **Should the group chat tab show a loading state while fetching backend sessions?** Yes — show skeleton/spinner during the API call.
