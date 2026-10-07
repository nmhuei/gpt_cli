"""Helpers for local gateway session title detection."""

import re
from typing import Any

from .message_utils import _extract_content


def _is_session_title_request(messages: list[dict[str, Any]], system_text: str = "") -> bool:
    s_low = (system_text or "").lower()
    if (
        "generate a concise title" in s_low
        or "generate a session title" in s_low
        or "generate the session title" in s_low
        or "concise title for an ai coding-assistant" in s_low
        or "concise title for the session" in s_low
    ):
        return True
    for m in messages:
        c = _extract_content(m.get("content", "")).lower()
        if (
            "generate the session title" in c
            or "generate a session title" in c
            or "concise title for an ai coding-assistant" in c
            or "concise title for this conversation" in c
            or "concise 3-4 word title" in c
        ):
            return True
    return False


def _generate_local_session_title(messages: list[dict[str, Any]]) -> str:
    for m in messages:
        c = _extract_content(m.get("content", ""))
        if '"text":' in c:
            matches = re.findall(r'"text":\s*"([^"]+)"', c)
            if matches:
                raw = matches[-1].strip()
                return " ".join(raw.split()[:6])
        lines = [line.strip() for line in c.splitlines() if line.strip()]
        if lines:
            return " ".join(lines[-1].split()[:6])
    return "Session"
