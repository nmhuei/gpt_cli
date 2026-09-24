from dataclasses import dataclass, asdict
from typing import Any

@dataclass
class SessionRecord:
    local_id: str
    conversation_id: str | None = None
    last_message_id: str | None = None
    tag: str | None = None
    model: str | None = None
    created_at: str = ""
    updated_at: str = ""
    turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
