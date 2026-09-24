import json
from pathlib import Path
from typing import Callable, Any
from .events import TelemetryEvent

class TelemetrySink:
    """Records real-time telemetry events to disk (live_telemetry.jsonl) and dispatches to listeners."""

    def __init__(self, log_dir: Path | str):
        self.log_dir = Path(log_dir).expanduser()
        self.log_file = self.log_dir / "live_telemetry.jsonl"
        self._listeners: list[Callable[[TelemetryEvent], None]] = []

    def add_listener(self, callback: Callable[[TelemetryEvent], None]) -> None:
        self._listeners.append(callback)

    def record(self, event: TelemetryEvent) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(line)

        for listener in self._listeners:
            try:
                listener(event)
            except Exception:
                pass
