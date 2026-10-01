"""Read BQA's redacted workspace activity feed as an observational side channel."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx

from .events import TelemetryEvent


class BQAActivityStream:
    """SSE client scoped to one persisted BQA chat/workspace identity.

    It never changes ChatGPT turn state.  A failed BQA observer is therefore
    observable but cannot turn a successful model response into a failure.
    """

    def __init__(self, endpoint: str, *, reconnect_delay: float = 1.0) -> None:
        self.endpoint = endpoint
        self.reconnect_delay = reconnect_delay

    @staticmethod
    def event_from_record(record: dict[str, Any]) -> TelemetryEvent:
        """Project BQA's already-redacted journal schema into common telemetry."""
        return TelemetryEvent("host_activity", {
            "source": "bqa",
            "chat_id": record.get("chat_id"),
            "id": record.get("interaction_id") or record.get("seq"),
            "action": record.get("event_action") or "host operation",
            "phase": record.get("operation_phase") or "progress",
            "outcome": record.get("event_outcome"),
            "severity": record.get("severity_text"),
        })

    async def run(self, chat_id: str, emit: Callable[[TelemetryEvent], None]) -> None:
        last_event_id: str | None = None
        while True:
            headers = {"accept": "text/event-stream"}
            if last_event_id:
                headers["last-event-id"] = last_event_id
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream(
                        "GET", self.endpoint, params={"chat_id": chat_id, "replay": 0}, headers=headers
                    ) as response:
                        response.raise_for_status()
                        event_name = ""
                        data_lines: list[str] = []
                        async for line in response.aiter_lines():
                            if line.startswith("id:"):
                                last_event_id = line.split(":", 1)[1].strip() or last_event_id
                            elif line.startswith("event:"):
                                event_name = line.split(":", 1)[1].strip()
                            elif line.startswith("data:"):
                                data_lines.append(line.split(":", 1)[1].strip())
                            elif not line:
                                if event_name == "workspace_log" and data_lines:
                                    try:
                                        record = json.loads("\n".join(data_lines))
                                    except json.JSONDecodeError:
                                        record = None
                                    if isinstance(record, dict) and record.get("chat_id") == chat_id:
                                        emit(self.event_from_record(record))
                                event_name = ""
                                data_lines = []
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                emit(TelemetryEvent("host_activity_error", {
                    "source": "bqa", "chat_id": chat_id, "error": str(exc),
                }))
            await asyncio.sleep(self.reconnect_delay)
