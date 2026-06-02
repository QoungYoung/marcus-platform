# -*- coding: utf-8 -*-
"""
JSONL-based session storage for Trading Agent.
类似于 Pi 的 session 存储，但用 Python 实现。
"""
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, asdict
from enum import Enum


class EntryType(str, Enum):
    MESSAGE = "message"
    COMPACTION = "compaction"
    BRANCH_SUMMARY = "branch_summary"
    MODEL_CHANGE = "model_change"
    THINKING_LEVEL_CHANGE = "thinking_level_change"
    CUSTOM = "custom"
    LEAF = "leaf"


@dataclass
class SessionEntry:
    id: str
    parent_id: Optional[str]
    type: str
    timestamp: str
    data: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "parentId": self.parent_id,
            "type": self.type,
            "timestamp": self.timestamp,
            **self.data,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SessionEntry":
        return cls(
            id=d["id"],
            parent_id=d.get("parentId"),
            type=d["type"],
            timestamp=d.get("timestamp", datetime.now().isoformat()),
            data={k: v for k, v in d.items() if k not in ("id", "parentId", "type", "timestamp")},
        )


@dataclass
class MessageEntry(SessionEntry):
    role: str
    content: str

    def __post_init__(self):
        self.type = EntryType.MESSAGE.value
        self.data = {"role": self.role, "content": self.content}


@dataclass
class CompactionEntry(SessionEntry):
    summary: str
    first_kept_entry_id: str
    tokens_before: int

    def __post_init__(self):
        self.type = EntryType.COMPACTION.value
        self.data = {
            "summary": self.summary,
            "firstKeptEntryId": self.first_kept_entry_id,
            "tokensBefore": self.tokens_before,
        }


@dataclass
class LeafEntry(SessionEntry):
    target_id: Optional[str]

    def __post_init__(self):
        self.type = EntryType.LEAF.value
        self.data = {"targetId": self.target_id}


class SessionStorage:
    """JSONL-based session storage."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def _get_session_file(self, session_id: str) -> Path:
        return self._get_session_dir(session_id) / "session.jsonl"

    def _get_metadata_file(self, session_id: str) -> Path:
        return self._get_session_dir(session_id) / "metadata.json"

    def create_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a new session."""
        if session_id is None:
            session_id = str(uuid.uuid4())

        session_dir = self._get_session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        metadata = {
            "id": session_id,
            "createdAt": datetime.now().isoformat(),
            "leafId": None,
        }

        with open(self._get_metadata_file(session_id), "w", encoding="utf-8") as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        # Create empty session file
        with open(self._get_session_file(session_id), "w", encoding="utf-8") as f:
            f.write("")

        return metadata

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session metadata."""
        metadata_file = self._get_metadata_file(session_id)
        if not metadata_file.exists():
            return None

        with open(metadata_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def get_leaf_id(self, session_id: str) -> Optional[str]:
        """Get current leaf ID."""
        metadata = self.get_session(session_id)
        return metadata.get("leafId") if metadata else None

    def set_leaf_id(self, session_id: str, leaf_id: Optional[str]) -> None:
        """Update leaf ID."""
        metadata = self.get_session(session_id)
        if metadata:
            metadata["leafId"] = leaf_id
            with open(self._get_metadata_file(session_id), "w", encoding="utf-8") as f:
                json.dump(metadata, f, ensure_ascii=False, indent=2)

    def append_entry(self, session_id: str, entry: SessionEntry) -> str:
        """Append an entry to session file."""
        session_file = self._get_session_file(session_id)

        with open(session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")

        return entry.id

    def get_entries(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all entries from session file."""
        session_file = self._get_session_file(session_id)
        if not session_file.exists():
            return []

        entries = []
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))

        return entries

    def get_entry(self, session_id: str, entry_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific entry."""
        entries = self.get_entries(session_id)
        for entry in entries:
            if entry.get("id") == entry_id:
                return entry
        return None

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all message entries."""
        entries = self.get_entries(session_id)
        return [e for e in entries if e.get("type") == EntryType.MESSAGE.value]

    def create_entry_id(self) -> str:
        """Generate a new entry ID."""
        return str(uuid.uuid4())

    def fork_session(self, source_session_id: str, target_session_id: str, entry_id: Optional[str] = None) -> None:
        """Fork a session from an entry point."""
        source_entries = self.get_entries(source_session_id)

        # Create target session
        self.create_session(target_session_id)

        target_entries = []
        if entry_id:
            # Fork from specific entry
            found = False
            for entry in source_entries:
                if entry.get("id") == entry_id:
                    found = True
                if found:
                    target_entries.append(entry)
        else:
            # Fork all entries
            target_entries = source_entries

        # Write entries to target session
        session_file = self._get_session_file(target_session_id)
        with open(session_file, "w", encoding="utf-8") as f:
            for entry in target_entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_branch(self, session_id: str, leaf_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get entries from root to leaf."""
        entries = self.get_entries(session_id)
        if not leaf_id:
            return entries

        # Find path to leaf
        path = []
        entry_map = {e.get("id"): e for e in entries}

        current_id = leaf_id
        while current_id:
            entry = entry_map.get(current_id)
            if entry:
                path.insert(0, entry)
                current_id = entry.get("parentId")
            else:
                break

        return path

    def get_path_to_root(self, session_id: str, leaf_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get path from leaf to root."""
        if not leaf_id:
            leaf_id = self.get_leaf_id(session_id)

        entries = self.get_entries(session_id)
        entry_map = {e.get("id"): e for e in entries}

        path = []
        current_id = leaf_id
        while current_id:
            entry = entry_map.get(current_id)
            if entry:
                path.insert(0, entry)
                current_id = entry.get("parentId")
            else:
                break

        return path

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all sessions."""
        sessions = []
        for session_dir in self.base_dir.iterdir():
            if session_dir.is_dir():
                metadata_file = session_dir / "metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, "r", encoding="utf-8") as f:
                        sessions.append(json.load(f))
        return sorted(sessions, key=lambda x: x.get("createdAt", ""), reverse=True)