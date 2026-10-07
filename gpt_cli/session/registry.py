import fcntl
import json
import os
import tempfile
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .models import SessionRecord
from ..config import defaults


class SessionLookupError(LookupError):
    """Raised when an explicit resume target does not exist."""


class SessionRegistryCorruptionError(RuntimeError):
    """Raised when the persisted registry cannot be parsed safely."""


class SessionDeleteError(RuntimeError):
    """Raised when local session cleanup fails before metadata removal."""


class SessionPartialDeleteError(RuntimeError):
    """Raised when logs are removed but registry metadata could not be saved."""


class SessionRegistry:
    """Manages tool-created sessions stored in tool_sessions.json with process-safe file locking."""

    def __init__(self, storage_dir: Path | str | None = None):
        self.storage_dir = Path(storage_dir or os.getenv("WEBGPT_SESSION_DIR") or defaults.DEFAULT_SESSION_DIR).expanduser()
        self.storage_file = self.storage_dir / "tool_sessions.json"
        self.lock_file = self.storage_dir / ".tool_sessions.lock"
        self._rlock = threading.RLock()
        self._flock_fd = None
        self._lock_count = 0
        self._data: dict[str, Any] = {"active_id": None, "sessions": {}}
        self._load()

    @contextmanager
    def _lock(self):
        with self._rlock:
            if self._lock_count == 0:
                lock_handle = None
                try:
                    self.storage_dir.mkdir(parents=True, exist_ok=True)
                    lock_handle = open(self.lock_file, "a+")
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                except OSError as exc:
                    if lock_handle is not None:
                        try:
                            lock_handle.close()
                        except OSError:
                            pass
                    self._flock_fd = None
                    raise OSError(
                        f"Failed to acquire session registry lock at {self.lock_file}"
                    ) from exc
                self._flock_fd = lock_handle
            self._lock_count += 1
            try:
                yield
            finally:
                self._lock_count -= 1
                if self._lock_count == 0:
                    if self._flock_fd:
                        try:
                            fcntl.flock(self._flock_fd.fileno(), fcntl.LOCK_UN)
                            self._flock_fd.close()
                        except Exception:
                            pass
                        self._flock_fd = None

    def _load(self) -> None:
        if self.storage_file.exists():
            try:
                content = self.storage_file.read_text(encoding="utf-8").strip()
                if content:
                    loaded = json.loads(content)
                    self._validate_loaded_data(loaded)
                    self._data = loaded
                else:
                    raise SessionRegistryCorruptionError(
                        f"Corrupt session registry: empty file {self.storage_file}. "
                        "Repair or remove the file explicitly before continuing."
                    )
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                # Do not replace malformed persisted state with an empty registry.
                # The bytes on disk are the evidence needed for recovery.
                raise SessionRegistryCorruptionError(
                    f"Corrupt session registry: {self.storage_file}. "
                    "Repair or remove the file explicitly before continuing."
                ) from exc
        else:
            self._data = {"active_id": None, "sessions": {}}

    def _validate_loaded_data(self, data: Any) -> None:
        if not isinstance(data, dict):
            raise SessionRegistryCorruptionError(
                f"Corrupt session registry: expected object root in {self.storage_file}."
            )

        sessions = data.get("sessions")
        if not isinstance(sessions, dict):
            raise SessionRegistryCorruptionError(
                f"Corrupt session registry: expected sessions mapping in {self.storage_file}."
            )

        active_id = data.get("active_id")
        if active_id is not None and not isinstance(active_id, str):
            raise SessionRegistryCorruptionError(
                f"Corrupt session registry: invalid active session in {self.storage_file}."
            )

        for local_id, record in sessions.items():
            if not isinstance(local_id, str) or not isinstance(record, dict):
                raise SessionRegistryCorruptionError(
                    f"Corrupt session registry: invalid session record in {self.storage_file}."
                )
            if record.get("local_id") != local_id:
                raise SessionRegistryCorruptionError(
                    f"Corrupt session registry: invalid session record in {self.storage_file}."
                )

            invalid = (
                ("local_id", str, False),
                ("conversation_id", str, True),
                ("last_message_id", str, True),
                ("tag", str, True),
                ("model", str, True),
                ("bqa_chat_id", str, True),
                ("bqa_workspace_path", str, True),
                ("created_at", str, False),
                ("updated_at", str, False),
                ("turns", int, False),
            )
            for field_name, field_type, optional in invalid:
                if field_name in record:
                    value = record[field_name]
                    if (optional and value is not None and not isinstance(value, field_type)) or (
                        not optional and not isinstance(value, field_type)
                    ):
                        raise SessionRegistryCorruptionError(
                            f"Corrupt session registry: invalid session record in {self.storage_file}."
                        )
            if "turns" in record and (not isinstance(record["turns"], int) or isinstance(record["turns"], bool)):
                raise SessionRegistryCorruptionError(
                    f"Corrupt session registry: invalid session record in {self.storage_file}."
                )
            if "pending_steers" in record and (
                not isinstance(record["pending_steers"], list)
                or any(not isinstance(prompt, str) for prompt in record["pending_steers"])
            ):
                raise SessionRegistryCorruptionError(
                    f"Corrupt session registry: invalid session record in {self.storage_file}."
                )
            try:
                SessionRecord(**record)
            except TypeError as exc:
                raise SessionRegistryCorruptionError(
                    f"Corrupt session registry: invalid session record in {self.storage_file}."
                ) from exc

    def _save(self) -> None:
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            tmp_file = self.storage_dir / f".tmp_{uuid.uuid4().hex}"
            try:
                tmp_file.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
                os.replace(tmp_file, self.storage_file)
            finally:
                if tmp_file.exists():
                    try:
                        tmp_file.unlink(missing_ok=True)
                    except Exception:
                        pass
        except OSError as exc:
            raise OSError(
                f"Failed to persist session registry at {self.storage_file}"
            ) from exc

    def get_active(self) -> SessionRecord | None:
        with self._lock():
            self._load()
            active_id = self._data.get("active_id")
            if active_id and active_id in self._data["sessions"]:
                return SessionRecord(**self._data["sessions"][active_id])
            if self._data.get("sessions"):
                latest_id = max(
                    self._data["sessions"].keys(),
                    key=lambda k: self._data["sessions"][k].get("updated_at", "")
                )
                self._data["active_id"] = latest_id
                self._save()
                return SessionRecord(**self._data["sessions"][latest_id])
            return None

    def set_active(self, local_id: str) -> None:
        with self._lock():
            self._load()
            if local_id in self._data["sessions"]:
                self._data["active_id"] = local_id
                self._save()

    def get_session(self, local_id: str) -> SessionRecord | None:
        with self._lock():
            self._load()
            raw = self._data.get("sessions", {}).get(local_id)
            if raw is not None:
                return SessionRecord(**raw)
            return None

    def require_session(self, session_id: str | None) -> SessionRecord:
        """Return an existing active or named session without creating one."""
        if session_id is None:
            session = self.get_active()
            if session is None:
                raise SessionLookupError(
                    "No active local session; run a fresh prompt or name a session"
                )
            return session

        session = self.get_session(session_id)
        if session is None:
            raise SessionLookupError(f"Local session does not exist: {session_id}")
        return session

    def create_session(self, local_id: str | None = None, tag: str | None = None, set_active: bool = True) -> SessionRecord:
        with self._lock():
            self._load()
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
            if set_active:
                self._data["active_id"] = new_id
            self._save()
            return rec

    def get_or_create(self, session_id: str | None = None, tag: str | None = None, set_active: bool = True) -> SessionRecord:
        with self._lock():
            self._load()
            if session_id:
                raw = self._data.get("sessions", {}).get(session_id)
                if raw is not None:
                    if set_active:
                        self._data["active_id"] = session_id
                        self._save()
                    return SessionRecord(**raw)
                return self.create_session(local_id=session_id, tag=tag, set_active=set_active)

            active_id = self._data.get("active_id")
            if active_id and active_id in self._data["sessions"]:
                return SessionRecord(**self._data["sessions"][active_id])

            if self._data.get("sessions"):
                latest_id = max(
                    self._data["sessions"].keys(),
                    key=lambda k: self._data["sessions"][k].get("updated_at", "")
                )
                if set_active:
                    self._data["active_id"] = latest_id
                    self._save()
                return SessionRecord(**self._data["sessions"][latest_id])

            return self.create_session(tag=tag, set_active=set_active)

    def update_turn(
        self,
        local_id: str,
        conversation_id: str,
        last_message_id: str,
        model: str | None = None
    ) -> SessionRecord | None:
        with self._lock():
            self._load()
            if local_id in self._data["sessions"]:
                s = self._data["sessions"][local_id]
                s["conversation_id"] = conversation_id
                s["last_message_id"] = last_message_id
                if model:
                    s["model"] = model
                s["turns"] = s.get("turns", 0) + 1
                s["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save()
                return SessionRecord(**s)
            return None

    def checkpoint(
        self,
        local_id: str,
        conversation_id: str | None,
        message_id: str | None,
        model: str | None = None,
    ) -> SessionRecord | None:
        """Persist an observed upstream pointer without counting a finished turn."""
        with self._lock():
            self._load()
            session = self._data.get("sessions", {}).get(local_id)
            if session is None:
                return None
            if conversation_id:
                session["conversation_id"] = conversation_id
            if message_id:
                session["last_message_id"] = message_id
            if model:
                session["model"] = model
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return SessionRecord(**session)

    def bind_workspace(
        self,
        local_id: str,
        *,
        chat_id: str | None,
        workspace_path: str | None,
    ) -> SessionRecord | None:
        """Persist the workspace identity returned by a host bind tool."""
        with self._lock():
            self._load()
            session = self._data.get("sessions", {}).get(local_id)
            if session is None:
                return None
            if chat_id:
                session["bqa_chat_id"] = chat_id
            if workspace_path:
                session["bqa_workspace_path"] = workspace_path
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return SessionRecord(**session)

    def preserve_pending_steers(self, local_id: str, prompts: list[str]) -> SessionRecord | None:
        """Persist accepted instructions that outlive a failed live-turn owner."""
        clean_prompts = [prompt.strip() for prompt in prompts if prompt.strip()]
        if not clean_prompts:
            return self.get_session(local_id)
        with self._lock():
            self._load()
            session = self._data.get("sessions", {}).get(local_id)
            if session is None:
                return None
            pending = session.setdefault("pending_steers", [])
            if not isinstance(pending, list):
                pending = session["pending_steers"] = []
            pending.extend(clean_prompts)
            session["updated_at"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return SessionRecord(**session)

    def take_pending_steers(self, local_id: str) -> list[str]:
        """Atomically hand persisted instructions back to a newly safe live owner."""
        with self._lock():
            self._load()
            session = self._data.get("sessions", {}).get(local_id)
            if session is None:
                return []
            pending = session.get("pending_steers", [])
            if not isinstance(pending, list):
                pending = []
            prompts = [prompt for prompt in pending if isinstance(prompt, str) and prompt.strip()]
            session["pending_steers"] = []
            if prompts:
                session["updated_at"] = datetime.now(timezone.utc).isoformat()
                self._save()
            return prompts

    def list_sessions(self) -> list[SessionRecord]:
        with self._lock():
            self._load()
            return [
                SessionRecord(**s)
                for s in sorted(
                    self._data.get("sessions", {}).values(),
                    key=lambda x: x.get("updated_at", ""),
                    reverse=True
                )
            ]

    def delete_session(self, local_id: str) -> bool:
        with self._lock():
            self._load()
            if local_id in self._data.get("sessions", {}):
                del self._data["sessions"][local_id]
                if self._data.get("active_id") == local_id:
                    if self._data["sessions"]:
                        latest_id = max(
                            self._data["sessions"].keys(),
                            key=lambda k: self._data["sessions"][k].get("updated_at", "")
                        )
                        self._data["active_id"] = latest_id
                    else:
                        self._data["active_id"] = None
                self._save()
                return True
            return False
