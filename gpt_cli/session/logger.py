import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from ..config import defaults
from ..telemetry.events import TelemetryEvent

class SessionLogManager:
    """Manages persistent turn transcripts, prompt/response pairs, and raw telemetry per session."""

    def __init__(self, base_dir: Path | str | None = None):
        self.base_dir = Path(base_dir or defaults.DEFAULT_SESSION_DIR).expanduser()

    def session_dir(self, local_id: str) -> Path:
        return self.base_dir / local_id

    def log_turn(
        self,
        local_id: str,
        turn_index: int,
        prompt: str,
        response: str,
        model: str,
        duration_ms: int,
        conversation_id: str | None = None,
        message_id: str | None = None,
        thinking: list[str] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        recovery: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        s_dir = self.session_dir(local_id)
        s_dir.mkdir(parents=True, exist_ok=True)
        history_file = s_dir / "history.jsonl"

        record = {
            "turn_index": turn_index,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "prompt": prompt,
            "response": response,
            "model": model,
            "duration_ms": duration_ms,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "thinking": thinking or [],
            "tool_calls": tool_calls or [],
            "recovery": recovery,
            "error": error,
        }

        with open(history_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def log_event(self, local_id: str, event: TelemetryEvent, turn_index: int) -> None:
        s_dir = self.session_dir(local_id)
        s_dir.mkdir(parents=True, exist_ok=True)
        events_file = s_dir / "events.jsonl"

        entry = {
            "turn_index": turn_index,
            "timestamp": event.timestamp,
            "event_type": event.event_type,
            "data": event.data,
        }

        with open(events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_history(self, local_id: str) -> list[dict[str, Any]]:
        history_file = self.session_dir(local_id) / "history.jsonl"
        if not history_file.exists():
            return []
        turns = []
        for line in history_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    turns.append(json.loads(line))
                except Exception:
                    pass
        return turns

    def get_events(self, local_id: str, turn_index: int | None = None) -> list[dict[str, Any]]:
        events_file = self.session_dir(local_id) / "events.jsonl"
        if not events_file.exists():
            return []
        events = []
        for line in events_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    ev = json.loads(line)
                    if turn_index is None or ev.get("turn_index") == turn_index:
                        events.append(ev)
                except Exception:
                    pass
        return events

    def format_readable_log(self, local_id: str, max_turns: int | None = None) -> str:
        history = self.get_history(local_id)
        if not history:
            return f"No logged history found for session {local_id}."

        if max_turns is not None:
            history = history[-max_turns:]

        lines = [
            "=" * 70,
            f"SESSION LOG: {local_id}",
            f"Location: {self.session_dir(local_id)}",
            "=" * 70,
        ]

        for h in history:
            idx = h.get("turn_index", 1)
            ts = h.get("timestamp", "")[:19]
            dur = h.get("duration_ms", 0)
            model = h.get("model", "unknown")
            lines.append(f"\n[TURN #{idx}] ({ts}) | Duration: {dur}ms | Model: {model}")
            lines.append(f"Conv ID: {h.get('conversation_id')} | Msg ID: {h.get('message_id')}")
            lines.append("-" * 70)
            lines.append(f"PROMPT:\n  {h.get('prompt', '').strip()}\n")
            
            thinking = h.get("thinking")
            if thinking:
                lines.append("THINKING:")
                for th in thinking:
                    lines.append(f"  • {th}")
                lines.append("")

            recovery = h.get("recovery")
            if isinstance(recovery, dict):
                lines.append(f"RECOVERY: {recovery.get('reason', 'server timeline recovered')}")
                lines.append("")

            tool_calls = h.get("tool_calls")
            if tool_calls:
                lines.append("TOOL CALLS:")
                for tc in tool_calls:
                    source = f" [{tc['source']}]" if tc.get("source") else ""
                    result = tc.get("result")
                    suffix = "" if result is None else f" → {str(result)[:240]}"
                    lines.append(f"  ⚡ {tc.get('tool')}{source}: {tc.get('payload')}{suffix}")
                lines.append("")

            lines.append(f"RESPONSE:\n  {h.get('response', '').strip()}")
            if h.get("error"):
                lines.append(f"\nERROR:\n  {h.get('error')}")
            lines.append("=" * 70)

        return "\n".join(lines)
