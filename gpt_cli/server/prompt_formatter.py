"""Prompt formatting helpers for gateway requests."""

import json
from typing import Any

from .message_utils import _extract_content


def _extract_openai_tool_history(message: dict[str, Any]) -> str:
    """Preserve OpenAI tool-call turns when assistant content is null."""
    parts: list[str] = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = function.get("name") or call.get("name") or "tool"
        arguments = function.get("arguments") or call.get("arguments") or {}
        call_id = call.get("id") or call.get("tool_call_id") or ""
        parts.append(f"[Tool Call: {name} call_id={call_id} arguments={arguments}]")
    if message.get("function_call") and isinstance(message["function_call"], dict):
        fn = message["function_call"]
        parts.append(f"[Tool Call: {fn.get('name', 'tool')} arguments={fn.get('arguments', {})}]")
    return "\n".join(parts)


def format_chat_messages(
    messages: list[dict[str, Any]],
    system_prompt: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> str:
    if not system_prompt and not tools and len(messages) == 1 and messages[0].get("role") == "user":
        return _extract_content(messages[0].get("content", "")).strip()

    rendered: list[str] = []
    sys_sections: list[str] = []
    if system_prompt and system_prompt.strip():
        sys_sections.append(system_prompt.strip())

    if tools:
        tool_descs: list[str] = []
        for t in tools:
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            name = fn.get("name") or t.get("name", "")
            desc = fn.get("description") or t.get("description", "")
            schema = fn.get("parameters") or fn.get("input_schema") or t.get("input_schema") or t.get("parameters") or {}
            tool_descs.append(f"- {name}: {desc} (Parameters: {json.dumps(schema)})")
        if tool_descs:
            sys_sections.append("Available tools:\n" + "\n".join(tool_descs))

    full_system = "\n\n".join(sys_sections)
    if full_system:
        rendered.append(f"[System Instruction]\n{full_system}")

    for msg in messages:
        role = msg.get("role", "user")
        content = _extract_content(msg.get("content", ""))
        tool_history = _extract_openai_tool_history(msg) if role == "assistant" else ""
        if tool_history:
            content = "\n".join(part for part in (content, tool_history) if part.strip())
        if not content.strip() and role not in {"tool", "function"}:
            continue
        if role == "system":
            rendered.append(f"[System Instruction]\n{content.strip()}")
        elif role == "user":
            rendered.append(f"[User]\n{content.strip()}")
        elif role == "assistant":
            rendered.append(f"[Assistant]\n{content.strip()}")
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id") or msg.get("id") or ""
            rendered.append(f"[Tool Result call_id={tool_call_id}]\n{content.strip()}")
        elif role == "function":
            name = msg.get("name") or ""
            rendered.append(f"[Function Result name={name}]\n{content.strip()}")
        else:
            rendered.append(f"[{role.capitalize()}]\n{content.strip()}")
    return "\n\n".join(rendered)
