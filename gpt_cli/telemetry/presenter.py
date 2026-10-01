"""Compact terminal rendering for normalized telemetry events.

The event stream remains the lossless machine interface.  This module is only
the human-facing projection: it shows progress without dumping tool payloads
or arbitrarily large results into a terminal.
"""

from __future__ import annotations

from typing import Any, TextIO

from .formatter import extract_tool_command_summary


def _one_line(value: Any, *, limit: int = 140) -> str:
    text = str(value or "").replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit - 1]}…"


def summarize_tool_result(output: Any) -> str:
    """Give a bounded, provider-neutral outcome for a tool completion."""
    if isinstance(output, dict):
        if output.get("ok") is True:
            return "ok"
        if output.get("ok") is False or output.get("error"):
            return _one_line(output.get("error") or output.get("message") or "failed")
        if "exit_code" in output:
            return f"exit {output.get('exit_code')}"
        if "status" in output:
            return _one_line(output["status"])
    text = _one_line(output)
    return text or "completed"


class TerminalTelemetryPresenter:
    """Render user-safe progress while keeping raw details in session logs."""

    def __init__(self, output: TextIO, *, color: bool = True) -> None:
        self.output = output
        self.color = color
        self._seen: set[tuple[object, ...]] = set()

    def _write(self, text: str, *, dim: bool = False, cyan: bool = False) -> None:
        if self.color and dim:
            text = f"\033[2m{text}\033[0m"
        elif self.color and cyan:
            text = f"\033[36m{text}\033[0m"
        self.output.write(text + "\n")
        self.output.flush()

    @staticmethod
    def _key(event_type: str, data: dict[str, Any]) -> tuple[object, ...] | None:
        event_id = data.get("id")
        if isinstance(event_id, str) and event_id:
            return (event_type, event_id)
        if event_type == "thinking":
            return (event_type, data.get("title"), str(data.get("thoughts")))
        if event_type == "reasoning_recap":
            return (event_type, data.get("recap"))
        if event_type == "recovery_started":
            return (event_type, data.get("reason"))
        return None

    def emit(self, event: Any) -> None:
        event_type = getattr(event, "event_type", "")
        data = getattr(event, "data", {})
        if not isinstance(data, dict):
            return
        key = self._key(event_type, data)
        if key is not None:
            if key in self._seen:
                return
            self._seen.add(key)

        recovered = " (recovered)" if data.get("source") == "dag" else ""
        if event_type == "recovery_started":
            self._write("↻ Recovering completed server activity…", dim=True)
        elif event_type == "thinking":
            title = _one_line(data.get("title"))
            summaries = [
                _one_line(item.get("summary") if isinstance(item, dict) else item)
                for item in data.get("thoughts", [])
            ]
            for summary in ([title] if title else []) + [item for item in summaries if item]:
                self._write(f"💭 {summary}{recovered}", dim=True)
        elif event_type == "reasoning_recap":
            recap = _one_line(data.get("recap"))
            if recap:
                self._write(f"⏱ {recap}{recovered}", dim=True)
        elif event_type == "tool_call_started":
            tool = str(data.get("tool") or "tool")
            summary = extract_tool_command_summary(tool, data.get("payload"))
            self._write(f"🔧 {summary}{recovered}", cyan=True)
        elif event_type == "tool_call_finished":
            tool = str(data.get("tool") or "tool")
            self._write(f"✓ {tool}: {summarize_tool_result(data.get('output'))}{recovered}", dim=True)
        elif event_type == "host_activity":
            action = _one_line(data.get("action") or "host operation")
            phase = _one_line(data.get("phase") or "progress")
            outcome = _one_line(data.get("outcome"))
            suffix = f" → {outcome}" if outcome else ""
            self._write(f"⚙ host: {action} ({phase}){suffix}", dim=True)
        elif event_type == "host_activity_error":
            self._write("⚠ host activity observer disconnected; retrying…", dim=True)
