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

    return body, None
