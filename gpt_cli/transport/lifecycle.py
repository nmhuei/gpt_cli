"""Classify a turn from observed transport and upstream state.

The classifier intentionally describes transport facts only.  In particular,
``model_terminal`` means that the upstream assistant emitted a terminal
message; it makes no assertion that the user's task was completed.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Iterable, Protocol


class LifecycleStatus(StrEnum):
    CONNECTOR_UNAVAILABLE = "connector_unavailable"
    SERVER_ACTIVE = "server_active"
    TOOL_TIMEOUT = "tool_timeout"
    TRANSPORT_LOST = "transport_lost"
    MODEL_TERMINAL = "model_terminal"
    UPSTREAM_ERROR = "upstream_error"
    COMPLETED_TURN = "completed_turn"


class EventLike(Protocol):
    event_type: str
    data: dict[str, Any]


def _tool_exit_code(data: dict[str, Any]) -> int | None:
    """Extract a process exit status only when it was supplied structurally."""
    for key in ("exit_code", "returncode", "status_code"):
        value = data.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    result = data.get("result")
    if isinstance(result, dict):
        return _tool_exit_code(result)
    return None


def _state_value(state: Any, name: str, default: Any = None) -> Any:
    if state is None:
        return default
    if isinstance(state, dict):
        return state.get(name, default)
    return getattr(state, name, default)


def classify_lifecycle(events: Iterable[EventLike], dag_state: Any | None) -> LifecycleStatus:
    """Return the strongest lifecycle conclusion supported by the evidence.

    Event errors are stronger than a final message, and a known timeout is
    stronger than generic turn completion.  A current upstream tool node is
    authoritative evidence that the server remains active even if the local
    SSE connection has ended.
    """
    event_list = list(events)

    if any(event.event_type == "error" for event in event_list):
        return LifecycleStatus.UPSTREAM_ERROR
    if any(
        event.event_type == "tool_call_finished" and _tool_exit_code(event.data) == 124
        for event in event_list
    ):
        return LifecycleStatus.TOOL_TIMEOUT
    if any(event.event_type == "transport_lost" for event in event_list):
        return LifecycleStatus.TRANSPORT_LOST

    role = _state_value(dag_state, "role")
    recipient = _state_value(dag_state, "recipient")
    content_type = _state_value(dag_state, "content_type")
    finish_type = _state_value(dag_state, "finish_type")
    if role == "assistant" and recipient not in (None, "", "all", "final"):
        return LifecycleStatus.SERVER_ACTIVE
    if role == "assistant" and content_type == "text" and finish_type:
        return LifecycleStatus.MODEL_TERMINAL

    if _state_value(dag_state, "connector_available") is False:
        return LifecycleStatus.CONNECTOR_UNAVAILABLE

    if any(event.event_type == "done" for event in event_list):
        return LifecycleStatus.COMPLETED_TURN
    return LifecycleStatus.TRANSPORT_LOST
