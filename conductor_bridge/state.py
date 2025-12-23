"""Atomic JSON state management with file locking."""

import json
import os
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field

# Windows-compatible file locking
if sys.platform == 'win32':
    import msvcrt
    def lock_file(f, exclusive=True):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK, 1)
        except (OSError, IOError):
            pass  # Best effort locking
    def unlock_file(f):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except (OSError, IOError):
            pass
else:
    import fcntl
    def lock_file(f, exclusive=True):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
    def unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class BridgeState(BaseModel):
    """Canonical state for the conductor bridge."""
    phase: str = Field(default="planning", description="Current phase: planning, implementing, awaiting_review")
    paused: bool = Field(default=False, description="Whether the loop is paused")
    cycle_count: int = Field(default=0, description="Number of completed cycles")
    last_updated: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    current_task: Optional[str] = Field(default=None, description="Current task description")
    error: Optional[str] = Field(default=None, description="Last error if any")


class Event(BaseModel):
    """Event for append-only event log."""
    timestamp: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    type: str
    payload: dict = Field(default_factory=dict)


class StateManager:
    """Thread-safe state management with atomic writes."""

    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "state.json"
        self.events_file = self.state_dir / "events.jsonl"
        self.artifacts_dir = self.state_dir / "artifacts"
        self._ensure_dirs()

    def _ensure_dirs(self):
        """Ensure all required directories exist."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _atomic_write(self, path: Path, content: str):
        """Write content atomically using temp file + rename."""
        fd, tmp_path = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def get_state(self) -> BridgeState:
        """Read current state with file locking."""
        if not self.state_file.exists():
            return BridgeState()

        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                lock_file(f, exclusive=False)
                try:
                    data = json.load(f)
                    return BridgeState(**data)
                finally:
                    unlock_file(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return BridgeState()

    def set_state(self, partial_update: dict) -> BridgeState:
        """Merge partial update into state and write atomically."""
        current = self.get_state()
        updated_data = current.model_dump()
        updated_data.update(partial_update)
        updated_data['last_updated'] = datetime.utcnow().isoformat()

        new_state = BridgeState(**updated_data)
        self._atomic_write(self.state_file, new_state.model_dump_json(indent=2))
        return new_state

    def append_event(self, event_type: str, payload: dict = None) -> Event:
        """Append event to events.jsonl."""
        event = Event(type=event_type, payload=payload or {})

        # Append with file locking
        with open(self.events_file, 'a', encoding='utf-8') as f:
            f.write(event.model_dump_json() + "\n")

        return event

    def get_events(self, limit: int = 100) -> list[Event]:
        """Read last N events from event log."""
        if not self.events_file.exists():
            return []

        events = []
        with open(self.events_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(Event(**json.loads(line)))
                    except (json.JSONDecodeError, ValueError):
                        continue

        return events[-limit:]

    def write_artifact(self, name: str, content: str):
        """Write an artifact file."""
        artifact_path = self.artifacts_dir / name
        self._atomic_write(artifact_path, content)

    def read_artifact(self, name: str) -> Optional[str]:
        """Read an artifact file."""
        artifact_path = self.artifacts_dir / name
        if artifact_path.exists():
            return artifact_path.read_text(encoding='utf-8')
        return None
