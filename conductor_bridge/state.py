"""Atomic JSON state management with file locking."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


def _utcnow_iso() -> str:
    return datetime.utcnow().isoformat()


# Windows-compatible file locking
if sys.platform == "win32":
    import msvcrt

    def lock_file(f, exclusive: bool = True) -> None:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBRLCK, 1)
        except (OSError, IOError):
            pass

    def unlock_file(f) -> None:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except (OSError, IOError):
            pass

else:
    import fcntl

    def lock_file(f, exclusive: bool = True) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)

    def unlock_file(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


@dataclass
class BridgeState:
    """Canonical state for the conductor bridge."""

    phase: str = "planning"
    paused: bool = False
    cycle_count: int = 0
    last_updated: str = field(default_factory=_utcnow_iso)
    current_task: Optional[str] = None
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BridgeState":
        return cls(
            phase=str(data.get("phase", "planning")),
            paused=bool(data.get("paused", False)),
            cycle_count=int(data.get("cycle_count", 0)),
            last_updated=str(data.get("last_updated", _utcnow_iso())),
            current_task=data.get("current_task", None),
            error=data.get("error", None),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Event:
    """Event for append-only event log."""

    type: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utcnow_iso)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(
            timestamp=str(data.get("timestamp", _utcnow_iso())),
            type=str(data.get("type", "unknown")),
            payload=dict(data.get("payload") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StateManager:
    """Thread-safe state management with atomic writes."""

    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_file = self.state_dir / "state.json"
        self.events_file = self.state_dir / "events.jsonl"
        self.artifacts_dir = self.state_dir / "artifacts"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _validate_artifact_name(self, name: str) -> str:
        if not name or not isinstance(name, str):
            raise ValueError("Artifact name must be a non-empty string")
        if Path(name).name != name:
            raise ValueError("Artifact name must be a filename (no directories)")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError("Artifact name contains invalid characters")
        return name

    def _atomic_write(self, path: Path, content: str) -> None:
        fd, tmp_path = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp_path, path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise

    def get_state(self) -> BridgeState:
        if not self.state_file.exists():
            return BridgeState()

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                lock_file(f, exclusive=False)
                try:
                    data = json.load(f)
                finally:
                    unlock_file(f)
            if isinstance(data, dict):
                return BridgeState.from_dict(data)
        except (json.JSONDecodeError, FileNotFoundError):
            pass
        return BridgeState()

    def set_state(self, partial_update: dict[str, Any]) -> BridgeState:
        current = self.get_state().to_dict()
        current.update(partial_update or {})
        current["last_updated"] = _utcnow_iso()
        new_state = BridgeState.from_dict(current)
        self._atomic_write(self.state_file, json.dumps(new_state.to_dict(), indent=2))
        return new_state

    def append_event(self, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        event = Event(type=event_type, payload=payload or {})
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")
        return event

    def get_events(self, limit: int = 100) -> list[Event]:
        if not self.events_file.exists():
            return []

        events: list[Event] = []
        with open(self.events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if isinstance(data, dict):
                        events.append(Event.from_dict(data))
                except (json.JSONDecodeError, ValueError):
                    continue
        return events[-limit:]

    def write_artifact(self, name: str, content: str) -> None:
        safe_name = self._validate_artifact_name(name)
        self._atomic_write(self.artifacts_dir / safe_name, content)

    def read_artifact(self, name: str) -> Optional[str]:
        safe_name = self._validate_artifact_name(name)
        p = self.artifacts_dir / safe_name
        return p.read_text(encoding="utf-8") if p.exists() else None
