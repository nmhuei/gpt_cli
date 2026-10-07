import uuid
from typing import Any

from ..models.registry import ModelRegistry
from ..core.types import SteerContext


def normalize_effort(effort: str | None, model_def: Any) -> str:
    chosen_effort = effort or model_def.default_effort
    if chosen_effort not in ("extended", "standard"):
        chosen_effort = "standard" if chosen_effort in ("low", "none", "off", "min", "medium") else "extended"
    return chosen_effort


def build_payload(prompt: str, conversation_id: str | None = None, parent_id: str | None = None,
                  model_alias: str | None = None, effort: str | None = None,
                  system_hints: list[str] | None = None,
                  selected_mcp_sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    model_def = ModelRegistry.resolve(model_alias)
    chosen_effort = normalize_effort(effort, model_def)
    msg_id = str(uuid.uuid4())
    message: dict[str, Any] = {"id": msg_id, "author": {"role": "user"}, "content": {"content_type": "text", "parts": [prompt]}}
    metadata: dict[str, Any] = {}
    if selected_mcp_sources:
        metadata["selected_mcp_sources"] = list(selected_mcp_sources)
    if model_def.supports_thinking:
        metadata["thinking_effort"] = chosen_effort
    if metadata:
        message["metadata"] = metadata
    payload: dict[str, Any] = {"action": "next", "messages": [message], "model": model_def.slug,
        "parent_message_id": parent_id or "client-created-root", "conversation_mode": {"kind": "primary_assistant"}}
    if model_def.supports_thinking:
        payload["thinking_effort"] = chosen_effort
    if system_hints:
        payload["system_hints"] = list(system_hints)
    if conversation_id and parent_id and parent_id != "client-created-root":
        payload["conversation_id"] = conversation_id
    else:
        payload["parent_message_id"] = "client-created-root"
    return payload
