"""Provider-neutral formatting for tool telemetry.

Formatting derives from payload/result shape.  It intentionally contains no
connector, workspace, CTF, or vendor-specific tool-name rules.
"""

from __future__ import annotations

import json
from typing import Any


_MAX_RENDERED_CHARS = 500


def _unwrap_payload(payload: Any) -> dict[str, Any]:
    if not payload:
        return {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return {"raw": payload}
    if not isinstance(payload, dict):
        return {"raw": payload}
    for wrapper in ("args", "body"):
        nested = payload.get(wrapper)
        if isinstance(nested, dict):
            inherited = {
                key: payload[key]
                for key in ("path", "action", "intent")
                if key in payload and key not in nested
            }
            return {**nested, **inherited}
    return payload


def _render(value: Any) -> str:
    if isinstance(value, str):
        rendered = value.strip()
    else:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(rendered) > _MAX_RENDERED_CHARS:
        return f"{rendered[:_MAX_RENDERED_CHARS]}\n... [truncated {len(rendered) - _MAX_RENDERED_CHARS} bytes]"
    return rendered


def format_tool_call(tool_name: str, raw_payload: Any) -> tuple[str, str]:
    """Return generic display data for any schema-advertised tool."""
    payload = _unwrap_payload(raw_payload)
    title = f"🔧 [Tool Call: {tool_name or 'tool'}]"
    command = payload.get("command") or payload.get("cmd")
    if isinstance(command, str):
        details = f"$ {command}"
        if payload.get("cwd"):
            details += f"\n(cwd: {payload['cwd']})"
        return title, details
    if not payload:
        return title, ""
    return title, f"```json\n{_render(payload)}\n```"


def extract_tool_command_summary(tool_name: str, raw_payload: Any) -> str:
    """Extract a concise single-line description of the tool execution."""
    payload = _unwrap_payload(raw_payload)
    cmd = (
        payload.get("command")
        or payload.get("cmd")
        or payload.get("command_line")
        or payload.get("CommandLine")
        or payload.get("query")
        or payload.get("path")
        or payload.get("paths")
        or payload.get("code")
    )
    if cmd:
        cmd_str = str(cmd).replace("\n", " ").strip()
        return f"{tool_name}: {cmd_str}"
    if payload:
        first_val = next(iter(payload.values()))
        if isinstance(first_val, (str, int, float)):
            return f"{tool_name}: {first_val}"
    return tool_name


def format_tool_output(tool_name: str, raw_output: Any, title: str | None = None) -> str:
    """Render output through common result fields, without inspecting names."""
    del tool_name, title
    data = raw_output
    if isinstance(raw_output, str):
        try:
            data = json.loads(raw_output)
        except json.JSONDecodeError:
            return _render(raw_output)

    if not isinstance(data, dict):
        return _render(data)
    if data.get("error") or data.get("ok") is False:
        error = data.get("error") or data.get("message") or "Unknown error"
        return f"❌ Error: {_render(error)}"
    if "stdout" in data or "stderr" in data or "exit_code" in data:
        stdout = str(data.get("stdout", "")).strip()
        stderr = str(data.get("stderr", "")).strip()
        code = data.get("exit_code", 0)
        if code not in (0, None):
            return f"❌ Exit code {code}:\n{_render(stderr or stdout)}"
        remaining = {key: value for key, value in data.items() if key not in {"stdout", "stderr"}}
        return _render(stdout or stderr or remaining)
    return f"```json\n{_render(data)}\n```"
