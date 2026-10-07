"""Durable ownership and queued steer state for one live local session."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


class SessionBusyError(RuntimeError):
    """Raised when another process already owns a live local session."""


class NoLiveTurnError(LookupError):
    """Raised when a requested live session no longer has an owner."""


class LiveTurnRegistryCorruptionError(RuntimeError):
    """Raised when live-turn state cannot be safely read."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _process_start_ticks(pid: int) -> str | None:
    """Return Linux process start ticks, or ``None`` on platforms without procfs."""
    try:
        tail = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return tail[19]
    except (FileNotFoundError, IndexError, OSError):
        return None


@dataclass(slots=True)
class TurnOwner:
    session_id: str
    owner_id: str
    pid: int
    process_start_ticks: str | None
    socket_path: str
    created_at: str

    @classmethod
    def create_for_current_process(cls, session_id: str, socket_path: Path) -> "TurnOwner":
        pid = os.getpid()
        return cls(
            session_id=session_id,
            owner_id=uuid.uuid4().hex,
            pid=pid,
            process_start_ticks=_process_start_ticks(pid),
            socket_path=str(socket_path),
            created_at=_utc_now(),
        )


@dataclass(slots=True)
class QueuedSteer:
    request_id: str
    prompt: str
    accepted_at: str
    state: str = "queued"


@dataclass(slots=True)
class LiveTurnRecord:
    session_id: str
    owner: TurnOwner
    queue: list[QueuedSteer] = field(default_factory=list)
    conversation_id: str | None = None
    parent_message_id: str | None = None
    model: str | None = None
    async_task_id: str | None = None
    async_task_turn_id: str | None = None
    turn_trace_id: str | None = None


class LiveTurnRegistry:
    """JSON-backed state separate from completed local-session records."""

    def __init__(self, runtime_dir: Path | str):
        self.runtime_dir = Path(runtime_dir).expanduser()
        self.state_file = self.runtime_dir / "live_turns.json"
        self.lock_file = self.runtime_dir / ".live_turns.lock"
        self._rlock = threading.RLock()
        self._data: dict[str, dict[str, object]] = {"turns": {}}

    @contextmanager
    def _lock(self):
        with self._rlock:
            self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                self.runtime_dir.chmod(0o700)
            except OSError:
                pass
            with open(self.lock_file, "a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _load(self) -> None:
        try:
            raw = self.state_file.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self._data = {"turns": {}}
            return
        except UnicodeDecodeError as exc:
            raise LiveTurnRegistryCorruptionError(
                f"Corrupt live-turn registry encoding: {self.state_file}"
            ) from exc
        except OSError as exc:
            raise LiveTurnRegistryCorruptionError(
                f"Unable to read live-turn registry {self.state_file}"
            ) from exc
        try:
            if not raw:
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: {self.state_file}"
                )
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LiveTurnRegistryCorruptionError(
                f"Corrupt live-turn registry: {self.state_file}"
            ) from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get("turns"), dict):
            raise LiveTurnRegistryCorruptionError(
                f"Corrupt live-turn registry: {self.state_file}"
            )
        self._data = parsed

    def _save(self) -> None:
        tmp_path = self.runtime_dir / f".live_turns.{uuid.uuid4().hex}.tmp"
        try:
            tmp_path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            os.replace(tmp_path, self.state_file)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @staticmethod
    def _record_from_raw(raw: dict[str, object]) -> LiveTurnRecord:
        owner_raw = raw.get("owner")
        if not isinstance(owner_raw, dict):
            raise ValueError("missing live-turn owner")
        owner = TurnOwner(**owner_raw)
        queue_raw = raw.get("queue")
        if queue_raw is None:
            queue = []
        elif not isinstance(queue_raw, list):
            raise ValueError("invalid live-turn queue")
        else:
            if any(not isinstance(entry, dict) for entry in queue_raw):
                raise ValueError("invalid live-turn queue entry")
            queue = [QueuedSteer(**entry) for entry in queue_raw]
        return LiveTurnRecord(
            session_id=str(raw["session_id"]),
            owner=owner,
            queue=queue,
            conversation_id=raw.get("conversation_id") if isinstance(raw.get("conversation_id"), str) else None,
            parent_message_id=raw.get("parent_message_id") if isinstance(raw.get("parent_message_id"), str) else None,
            model=raw.get("model") if isinstance(raw.get("model"), str) else None,
            async_task_id=raw.get("async_task_id") if isinstance(raw.get("async_task_id"), str) else None,
            async_task_turn_id=raw.get("async_task_turn_id") if isinstance(raw.get("async_task_turn_id"), str) else None,
            turn_trace_id=raw.get("turn_trace_id") if isinstance(raw.get("turn_trace_id"), str) else None,
        )

    @staticmethod
    def _owner_alive(owner: TurnOwner) -> bool:
        try:
            os.kill(owner.pid, 0)
        except OSError:
            return False
        if owner.process_start_ticks is None:
            return True
        return _process_start_ticks(owner.pid) == owner.process_start_ticks

    def _cleanup_stale_loaded(self) -> list[str]:
        turns = self._data["turns"]
        assert isinstance(turns, dict)
        stale_ids: list[str] = []
        for session_id, raw in list(turns.items()):
            if not isinstance(raw, dict):
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
                )
            try:
                record = self._record_from_raw(raw)
            except (KeyError, TypeError, ValueError) as exc:
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
                ) from exc
            if record is not None and self._owner_alive(record.owner):
                continue
            stale_ids.append(str(session_id))
            if record is not None:
                try:
                    Path(record.owner.socket_path).unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    (self.runtime_dir / f"{session_id}.sock").unlink(missing_ok=True)
                except OSError:
                    pass
            del turns[session_id]
        return stale_ids

    def cleanup_stale(self) -> list[str]:
        with self._lock():
            self._load()
            stale = self._cleanup_stale_loaded()
            if stale:
                self._save()
            return stale

    def claim(self, owner: TurnOwner) -> LiveTurnRecord:
        with self._lock():
            self._load()
            changed = bool(self._cleanup_stale_loaded())
            turns = self._data["turns"]
            assert isinstance(turns, dict)
            existing = turns.get(owner.session_id)
            if isinstance(existing, dict):
                record = self._record_from_raw(existing)
                if record.owner.owner_id == owner.owner_id:
                    if changed:
                        self._save()
                    return record
                if changed:
                    self._save()
                raise SessionBusyError(f"Local session is already running: {owner.session_id}")
            record = LiveTurnRecord(session_id=owner.session_id, owner=owner)
            turns[owner.session_id] = asdict(record)
            self._save()
            return record

    def get_live(self, session_id: str) -> LiveTurnRecord | None:
        with self._lock():
            self._load()
            changed = bool(self._cleanup_stale_loaded())
            turns = self._data["turns"]
            assert isinstance(turns, dict)
            raw = turns.get(session_id)
            if changed:
                self._save()
            if raw is None:
                return None
            if not isinstance(raw, dict):
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
                )
            try:
                return self._record_from_raw(raw)
            except (TypeError, ValueError, KeyError) as exc:
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
                ) from exc

    def list_live(self) -> list[LiveTurnRecord]:
        with self._lock():
            self._load()
            changed = bool(self._cleanup_stale_loaded())
            turns = self._data["turns"]
            assert isinstance(turns, dict)
            try:
                records = [self._record_from_raw(raw) for raw in turns.values()]
            except (TypeError, ValueError, KeyError) as exc:
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
                ) from exc
            if changed:
                self._save()
            return records

    def _require_raw(self, session_id: str) -> dict[str, object]:
        turns = self._data["turns"]
        assert isinstance(turns, dict)
        if session_id not in turns:
            raise NoLiveTurnError(f"No live turn for local session: {session_id}")
        raw = turns[session_id]
        if not isinstance(raw, dict):
            raise LiveTurnRegistryCorruptionError(
                f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
            )
        return raw

    def enqueue(self, session_id: str, request_id: str, prompt: str) -> QueuedSteer:
        clean_prompt = prompt.strip()
        if not clean_prompt:
            raise ValueError("Steer prompt must not be empty")
        with self._lock():
            self._load()
            if self._cleanup_stale_loaded():
                self._save()
            raw = self._require_raw(session_id)
            queue = raw.setdefault("queue", [])
            assert isinstance(queue, list)
            for entry in queue:
                if not isinstance(entry, dict):
                    raise LiveTurnRegistryCorruptionError(
                        f"Corrupt live-turn registry: invalid queued steer entry in {self.state_file}"
                    )
                if entry.get("request_id") == request_id:
                    return QueuedSteer(**entry)
            queued = QueuedSteer(request_id=request_id, prompt=clean_prompt, accepted_at=_utc_now())
            queue.append(asdict(queued))
            self._save()
            return queued

    def claim_next_queued(self, session_id: str) -> QueuedSteer | None:
        with self._lock():
            self._load()
            if self._cleanup_stale_loaded():
                self._save()
            raw = self._require_raw(session_id)
            queue = raw.setdefault("queue", [])
            assert isinstance(queue, list)
            for entry in queue:
                if not isinstance(entry, dict):
                    raise LiveTurnRegistryCorruptionError(
                        f"Corrupt live-turn registry: invalid queued steer entry in {self.state_file}"
                    )
                if entry.get("state") == "queued":
                    entry["state"] = "claimed"
                    self._save()
                    return QueuedSteer(**entry)
            return None

    def complete_queued(self, session_id: str, request_id: str) -> None:
        with self._lock():
            self._load()
            raw = self._require_raw(session_id)
            queue = raw.setdefault("queue", [])
            assert isinstance(queue, list)
            for index, entry in enumerate(queue):
                if not isinstance(entry, dict):
                    raise LiveTurnRegistryCorruptionError(
                        f"Corrupt live-turn registry: invalid queued steer entry in {self.state_file}"
                    )
                if entry.get("request_id") == request_id and entry.get("state") == "claimed":
                    del queue[index]
                    self._save()
                    return
            raise LookupError(f"No claimed steer request {request_id} for {session_id}")

    def queued_prompts(self, session_id: str) -> list[str]:
        """Return every accepted instruction, including a currently claimed one."""
        with self._lock():
            self._load()
            raw = self._require_raw(session_id)
            queue = raw.get("queue", [])
            if not isinstance(queue, list):
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid steer queue in {self.state_file}"
                )
            prompts = []
            for entry in queue:
                if not isinstance(entry, dict):
                    raise LiveTurnRegistryCorruptionError(
                        f"Corrupt live-turn registry: invalid queued steer entry in {self.state_file}"
                    )
                prompt = entry.get("prompt")
                if isinstance(prompt, str) and prompt.strip():
                    prompts.append(prompt)
            return prompts

    def update_upstream(
        self,
        session_id: str,
        *,
        conversation_id: str | None = None,
        parent_message_id: str | None = None,
        model: str | None = None,
        async_task_id: str | None = None,
        async_task_turn_id: str | None = None,
        turn_trace_id: str | None = None,
    ) -> LiveTurnRecord:
        with self._lock():
            self._load()
            raw = self._require_raw(session_id)
            for key, value in {
                "conversation_id": conversation_id,
                "parent_message_id": parent_message_id,
                "model": model,
                "async_task_id": async_task_id,
                "async_task_turn_id": async_task_turn_id,
                "turn_trace_id": turn_trace_id,
            }.items():
                if value is not None:
                    raw[key] = value
            self._save()
            return self._record_from_raw(raw)

    def update_socket_path(self, session_id: str, owner_id: str, socket_path: str | Path) -> bool:
        with self._lock():
            self._load()
            turns = self._data.get("turns")
            if not isinstance(turns, dict):
                return False
            if session_id not in turns:
                return False
            raw = turns[session_id]
            if not isinstance(raw, dict):
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
                )
            owner_raw = raw.get("owner")
            if owner_raw is not None and not isinstance(owner_raw, dict):
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid owner entry in {self.state_file}"
                )
            if isinstance(owner_raw, dict) and owner_raw.get("owner_id") == owner_id:
                owner_raw["socket_path"] = str(socket_path)
                self._save()
                return True
            return False

    def has_live(self, session_id: str) -> bool:
        """Return whether a live owner currently exists after stale cleanup."""
        return self.get_live(session_id) is not None

    def delete_if_idle(self, session_id: str, delete_callback) -> bool:
        """Run deletion while holding the live-turn lock so a new claim cannot race it."""
        with self._lock():
            self._load()
            changed = bool(self._cleanup_stale_loaded())
            turns = self._data["turns"]
            assert isinstance(turns, dict)
            if session_id in turns:
                if changed:
                    self._save()
                raise SessionBusyError(f"Cannot delete live session: {session_id}")
            deleted = delete_callback()
            if changed:
                self._save()
            return deleted

    def release(self, session_id: str, owner_id: str) -> bool:
        with self._lock():
            self._load()
            turns = self._data["turns"]
            assert isinstance(turns, dict)
            raw = turns.get(session_id)
            if raw is None:
                return False
            if not isinstance(raw, dict):
                raise LiveTurnRegistryCorruptionError(
                    f"Corrupt live-turn registry: invalid live turn entry in {self.state_file}"
                )
            record = self._record_from_raw(raw)
            if record.owner.owner_id != owner_id:
                return False
            del turns[session_id]
            self._save()
            try:
                Path(record.owner.socket_path).unlink(missing_ok=True)
            except OSError:
                pass
            try:
                (self.runtime_dir / f"{session_id}.sock").unlink(missing_ok=True)
            except OSError:
                pass
            return True
