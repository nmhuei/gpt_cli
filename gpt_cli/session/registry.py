import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .models import SessionRecord
from ..config import defaults

class SessionRegistry:
    """Manages tool-created sessions stored in tool_sessions.json."""

    def __init__(self, storage_dir: Path | str | None = None):
        self.storage_dir = Path(storage_dir or defaults.DEFAULT_SESSION_DIR).expanduser()
        self.storage_file = self.storage_dir / "tool_sessions.json"
        self._data: dict[str, Any] = {"active_id": None, "sessions": {}}
        self._load()

    def _load(self) -> None:
        if self.storage_file.exists():
            try:
                self._data = json.loads(self.storage_file.read_text(encoding="utf-8"))
            except Exception:
                self._data = {"active_id": None, "sessions": {}}
        else:
            self._data = {"active_id": None, "sessions": {}}

    def _save(self) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.storage_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")

    def get_active(self) -> SessionRecord | None:
        active_id = self._data.get("active_id")
        if not active_id or active_id not in self._data["sessions"]:
            return None
        return SessionRecord(**self._data["sessions"][active_id])

    def set_active(self, local_id: str) -> None:
        if local_id in self._data["sessions"]:
            self._data["active_id"] = local_id
            self._save()

    def get_session(self, local_id: str) -> SessionRecord | None:
        raw = self._data.get("sessions", {}).get(local_id)
        if raw is not None:
            return SessionRecord(**raw)
        return None

    def create_session(self, local_id: str | None = None, tag: str | None = None) -> SessionRecord:
        new_id = local_id or f"sess_{uuid.uuid4().hex[:8]}"
        now = datetime.now(timezone.utc).isoformat()
        rec = SessionRecord(
            local_id=new_id,
            tag=tag,
            created_at=now,
            updated_at=now,
            turns=0
        )
        self._data["sessions"][new_id] = asdict(rec)
        self._data["active_id"] = new_id
        self._save()
        return rec

    def get_or_create(self, session_id: str | None = None, tag: str | None = None) -> SessionRecord:
        if session_id:
            existing = self.get_session(session_id)
            if existing:
                self._data["active_id"] = session_id
                self._save()
                return existing
            return self.create_session(local_id=session_id, tag=tag)

        active = self.get_active()
        if active is not None:
            return active

        return self.create_session(tag=tag)

    def update_turn(
        self,
        local_id: str,
        conversation_id: str,
        last_message_id: str,
        model: str | None = None
    ) -> None:
        if local_id in self._data["sessions"]:
            s = self._data["sessions"][local_id]
            s["conversation_id"] = conversation_id
            s["last_message_id"] = last_message_id
            if model:
                s["model"] = model
            s["turns"] = s.get("turns", 0) + 1
            s["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()

    def list_sessions(self) -> list[SessionRecord]:
        return [
            SessionRecord(**s)
            for s in sorted(
                self._data.get("sessions", {}).values(),
                key=lambda x: x.get("updated_at", ""),
                reverse=True
            )
        ]

    def delete_session(self, local_id: str) -> bool:
        if local_id in self._data.get("sessions", {}):
            del self._data["sessions"][local_id]
            if self._data.get("active_id") == local_id:
                remaining = list(self._data["sessions"].keys())
                self._data["active_id"] = remaining[0] if remaining else None
            self._save()
            return True
        return False
