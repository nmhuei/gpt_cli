"""Shared gateway validation and SSE queue helpers."""

import asyncio
from typing import Any

from starlette.responses import JSONResponse


class SSEQueueOverflow(RuntimeError):
    """Raised when SSE backpressure cannot preserve ordered delivery."""


def _safe_queue_put(queue: asyncio.Queue, item: Any) -> None:
    """Enqueue an SSE frame or abort rather than silently dropping data."""
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull as exc:
        raise SSEQueueOverflow(
            "SSE consumer fell behind; aborting to preserve frames"
        ) from exc


def _gateway_error(message: str, *, anthropic: bool) -> JSONResponse:
    if anthropic:
        return JSONResponse(
            {
                "type": "error",
                "error": {"type": "invalid_request_error", "message": message},
            },
            status_code=400,
        )
    return JSONResponse({"error": {"message": message}}, status_code=400)


def _validate_gateway_messages(
    body: Any, *, anthropic: bool = False
) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """Validate gateway request envelope and message entries."""
    if not isinstance(body, dict):
        return None, _gateway_error(
            "Request body must be an object", anthropic=anthropic
        )

    messages = body.get("messages")
    if messages == [] or messages is None:
        return None, _gateway_error("Messages cannot be empty", anthropic=anthropic)
    if not isinstance(messages, list):
        return None, _gateway_error(
            "Messages must be a non-empty array", anthropic=anthropic
        )

    if any(
        not isinstance(message, dict)
        or not isinstance(message.get("role"), str)
        or "content" not in message
        for message in messages
    ):
        return None, _gateway_error(
            "Messages must contain role and content", anthropic=anthropic
        )

    def _validate_content_blocks(value):
        if not isinstance(value, (str, list)):
            return False
        if isinstance(value, list):
            for block in value:
                if not isinstance(block, dict):
                    return False
                if block.get("type") == "text" and not isinstance(block.get("text"), str):
                    return False
                if block.get("type") == "tool_result" and not _validate_content_blocks(block.get("content", "")):
                    return False
        return True

    for message in messages:
        content = message.get("content")
        has_openai_tool_call = (
            not anthropic
            and message.get("role") == "assistant"
            and (
                bool(message.get("tool_calls"))
                or bool(message.get("function_call"))
            )
        )
        if content is None and has_openai_tool_call:
            continue
        if not _validate_content_blocks(content):
            return None, _gateway_error(
                "Text content must be a string", anthropic=anthropic
            )

    if "tools" in body:
        tools = body.get("tools")
        if not isinstance(tools, list) or any(not isinstance(tool, dict) for tool in tools):
            return None, _gateway_error(
                "Tools must be an array of objects", anthropic=anthropic
            )
        for tool in tools:
            name = tool.get("name")
            if isinstance(name, str) and name.strip():
                continue
            fn = tool.get("function")
            if isinstance(fn, dict) and isinstance(fn.get("name"), str) and fn.get("name", "").strip():
                continue
            return None, _gateway_error(
                "Each tool must have a non-empty string name", anthropic=anthropic
            )

    metadata = body.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        return None, _gateway_error(
            "Metadata must be an object", anthropic=anthropic
        )

    if "model" in body and not isinstance(body.get("model"), str):
        return None, _gateway_error(
            "Model must be a string", anthropic=anthropic
        )

    if "stream" in body and not isinstance(body.get("stream"), bool):
        return None, _gateway_error(
            "Stream must be a boolean", anthropic=anthropic
        )

    return body, None
