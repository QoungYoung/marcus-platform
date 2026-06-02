# -*- coding: utf-8 -*-
"""
Session management for Trading Agent.
"""
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from app.agent.storage import SessionStorage, SessionEntry, MessageEntry, LeafEntry, EntryType


class Session:
    """Represents a trading agent session."""

    def __init__(self, storage: SessionStorage, session_id: str):
        self.storage = storage
        self.session_id = session_id
        self._ensure_session()

    def _ensure_session(self):
        """Ensure session exists."""
        if not self.storage.get_session(self.session_id):
            self.storage.create_session(self.session_id)

    @property
    def leaf_id(self) -> Optional[str]:
        return self.storage.get_leaf_id(self.session_id)

    def append_message(self, role: str, content: str) -> str:
        """Append a message entry."""
        parent_id = self.leaf_id
        entry_id = self.storage.create_entry_id()

        entry = MessageEntry(
            id=entry_id,
            parent_id=parent_id,
            type=EntryType.MESSAGE.value,
            timestamp=datetime.now().isoformat(),
            role=role,
            content=content,
        )

        self.storage.append_entry(self.session_id, entry)
        self.storage.set_leaf_id(self.session_id, entry_id)

        return entry_id

    def append_compaction(self, summary: str, first_kept_entry_id: str, tokens_before: int) -> str:
        """Append a compaction entry."""
        parent_id = self.leaf_id
        entry_id = self.storage.create_entry_id()

        entry = SessionEntry(
            id=entry_id,
            parent_id=parent_id,
            type=EntryType.COMPACTION.value,
            timestamp=datetime.now().isoformat(),
            data={
                "summary": summary,
                "firstKeptEntryId": first_kept_entry_id,
                "tokensBefore": tokens_before,
            },
        )

        self.storage.append_entry(self.session_id, entry)
        self.storage.set_leaf_id(self.session_id, entry_id)

        return entry_id

    def move_to(self, entry_id: str) -> Optional[str]:
        """Move leaf to a different entry (branch navigation)."""
        entry = self.storage.get_entry(self.session_id, entry_id)
        if entry:
            self.storage.set_leaf_id(self.session_id, entry_id)
            return entry_id
        return None

    def get_messages(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get messages for LLM context."""
        messages = self.storage.get_messages(self.session_id)

        # Filter for LLM - only role and content
        result = []
        for m in messages:
            # Skip compaction summaries as they're already summarized
            if m.get("type") == EntryType.COMPACTION.value:
                continue
            if m.get("type") == EntryType.MESSAGE.value:
                result.append({
                    "role": m.get("role"),
                    "content": m.get("content"),
                })

        if limit:
            result = result[-limit:]

        return result

    def get_full_context(self, include_system: bool = True) -> List[Dict[str, Any]]:
        """Get full context including all message types."""
        entries = self.storage.get_path_to_root(self.session_id, self.leaf_id)

        context = []
        for entry in entries:
            if entry.get("type") == EntryType.MESSAGE.value:
                context.append({
                    "role": entry.get("role"),
                    "content": entry.get("content"),
                    "timestamp": entry.get("timestamp"),
                })
            elif entry.get("type") == EntryType.COMPACTION.value:
                context.append({
                    "role": "system",
                    "content": f"[Earlier conversation summarized: {entry.get('summary')}]",
                    "timestamp": entry.get("timestamp"),
                })

        return context

    def get_entry_count(self) -> int:
        """Get total entry count."""
        return len(self.storage.get_entries(self.session_id))

    def compact(self, summary: str, first_kept_entry_id: str, tokens_before: int) -> str:
        """Create a compaction entry and remove entries before it."""
        # Get entries before first_kept
        all_entries = self.storage.get_entries(self.session_id)
        entries_to_remove = []

        found_first_kept = False
        for entry in all_entries:
            if entry.get("id") == first_kept_entry_id:
                found_first_kept = True
            if not found_first_kept:
                entries_to_remove.append(entry.get("id"))

        # Create compaction entry
        compaction_id = self.append_compaction(summary, first_kept_entry_id, tokens_before)

        # Note: actual file compaction is done separately to avoid data loss
        # The compaction entry marks where the summary starts

        return compaction_id

    def fork(self, target_session_id: str, from_entry_id: Optional[str] = None) -> "Session":
        """Fork this session to a new session."""
        self.storage.fork_session(self.session_id, target_session_id, from_entry_id)
        return Session(self.storage, target_session_id)

    def get_metadata(self) -> Dict[str, Any]:
        """Get session metadata."""
        return self.storage.get_session(self.session_id) or {}


class SessionManager:
    """Manages multiple sessions."""

    def __init__(self, base_dir: Path):
        self.storage = SessionStorage(base_dir)

    def create_session(self, session_id: Optional[str] = None) -> Session:
        """Create a new session."""
        if session_id is None:
            import uuid
            session_id = str(uuid.uuid4())

        session = Session(self.storage, session_id)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Get existing session."""
        if self.storage.get_session(session_id):
            return Session(self.storage, session_id)
        return None

    def get_or_create_session(self, session_id: str) -> Session:
        """Get session or create if doesn't exist."""
        return Session(self.storage, session_id)

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions."""
        return self.storage.list_sessions()

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        import shutil
        session_dir = self.storage._get_session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)
            return True
        return False