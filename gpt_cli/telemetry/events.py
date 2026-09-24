from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

@dataclass(slots=True)
class TelemetryEvent:
    event_type: str  # "thinking", "commentary", "web_search", "tool_call_started", "delta", "conversation_id", "message_id", "done", "error"
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
