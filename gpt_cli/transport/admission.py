"""Cross-process admission control for direct ChatGPT Web turns."""

from __future__ import annotations

import fcntl
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import defaults


class UpstreamCooldownError(RuntimeError):
    """The upstream rejected recent traffic and the shared cooldown is active."""

    def __init__(self, retry_at: float, reason: str):
        self.retry_at = retry_at
        self.reason = reason
        retry_at_text = datetime.fromtimestamp(retry_at, timezone.utc).isoformat()
        super().__init__(f"Upstream cooldown active; retry after {retry_at_text}: {reason}")


class UpstreamAdmission:
    """Persist request spacing and rejection cooldowns for every local process."""

    def __init__(
        self,
        runtime_dir: Path | str | None = None,
        *,
        request_interval_seconds: float | None = None,
        rejection_cooldown_seconds: float | None = None,
    ):
        self.runtime_dir = Path(runtime_dir or defaults.user_runtime_dir()).expanduser()
        self.state_file = self.runtime_dir / "upstream_admission.json"
        self.lock_file = self.runtime_dir / ".upstream_admission.lock"
        self.request_interval_seconds = request_interval_seconds if request_interval_seconds is not None else float(
            os.getenv("WEBGPT_REQUEST_INTERVAL_SECONDS", "15")
        )
        self.rejection_cooldown_seconds = (
            rejection_cooldown_seconds if rejection_cooldown_seconds is not None else float(
                os.getenv("WEBGPT_REJECTION_COOLDOWN_SECONDS", "900")
            )
        )

    @contextmanager
    def _locked_state(self):
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.runtime_dir.chmod(0o700)
        except OSError:
            pass
        with self.lock_file.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            state: dict[str, Any] = {}
            try:
                raw = self.state_file.read_text(encoding="utf-8")
                loaded = json.loads(raw) if raw.strip() else {}
                if isinstance(loaded, dict):
                    state = loaded
            except (OSError, json.JSONDecodeError):
                pass
            yield state
            temp = self.runtime_dir / ".upstream_admission.tmp"
            temp.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
            os.replace(temp, self.state_file)

    def reserve(self, *, now: float | None = None) -> float:
        """Reserve the next start slot and return the wait duration in seconds."""
        current = time.time() if now is None else now
        with self._locked_state() as state:
            cooldown_until = float(state.get("cooldown_until", 0.0))
            if cooldown_until > current:
                raise UpstreamCooldownError(cooldown_until, str(state.get("reason", "upstream rejected traffic")))
            if cooldown_until:
                state.pop("cooldown_until", None)
                state.pop("reason", None)
            start_at = max(current, float(state.get("next_request_at", 0.0)))
            state["next_request_at"] = start_at + max(0.0, self.request_interval_seconds)
            return start_at - current

    def record_rejection(
        self,
        reason: str,
        *,
        now: float | None = None,
        cooldown_seconds: float | None = None,
    ) -> float:
        """Open a shared cooldown after a server-side anti-abuse/rate response."""
        current = time.time() if now is None else now
        duration = self.rejection_cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        retry_at = current + max(0.0, duration)
        with self._locked_state() as state:
            state["cooldown_until"] = max(float(state.get("cooldown_until", 0.0)), retry_at)
            state["reason"] = reason[:300]
            state["next_request_at"] = max(float(state.get("next_request_at", 0.0)), retry_at)
            return float(state["cooldown_until"])


def is_upstream_rejection(error: str | None) -> bool:
    if not error:
        return False
    message = error.lower()
    return any(marker in message for marker in (
        "unusual activity",
        "rate limit",
        "too many requests",
        "limit of messages per hour",
        "http 429",
    ))
