from dataclasses import dataclass, field
from typing import Any

@dataclass(slots=True)
class TokenBundle:
    access_token: str
    cookies: dict[str, str] = field(default_factory=dict)
    oai_device_id: str | None = None
    cf_clearance: str | None = None
    chatgpt_account_id: str | None = None

@dataclass(slots=True)
class TurnRequest:
    prompt: str
    conversation_id: str | None = None
    parent_message_id: str | None = None
    model: str | None = None
    thinking_effort: str | None = None
    timeout_seconds: float = 60.0

@dataclass(slots=True)
class TurnResult:
    text: str
    conversation_id: str
    message_id: str
    model: str
    duration_ms: int = 0
    status: str = "completed"
    error: str | None = None
