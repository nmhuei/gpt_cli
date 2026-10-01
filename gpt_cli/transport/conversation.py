"""Read the current upstream conversation node after local stream uncertainty."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from curl_cffi.requests import AsyncSession

from ..config import defaults
from ..core.types import TokenBundle
from .conduit import ConduitHandshake


@dataclass(frozen=True, slots=True)
class ConversationState:
    node_id: str | None = None
    parent_id: str | None = None
    role: str | None = None
    recipient: str | None = None
    content_type: str | None = None
    message_status: str | None = None
    finish_type: str | None = None
    connector_available: bool | None = None
    text: str = ""
    matches_expected_message: bool | None = None


class ConversationRecovery:
    """Fetch and normalize only the current node of a server-side DAG."""

    def __init__(
        self,
        bundle: TokenBundle,
        session: AsyncSession | None = None,
        user_agent: str | None = None,
    ) -> None:
        self.bundle = bundle
        self.session = session or AsyncSession(impersonate="chrome146")
        self._owns_session = session is None
        self.user_agent = user_agent

    async def close(self) -> None:
        if self._owns_session:
            await self.session.close()

    async def fetch(
        self,
        conversation_id: str,
        *,
        expected_user_message_id: str | None = None,
    ) -> ConversationState:
        target_path = f"/backend-api/conversation/{conversation_id}"
        headers = ConduitHandshake.build_integrity_headers(
            self.bundle,
            str(uuid.uuid4()),
            str(uuid.uuid4()),
            user_agent=self.user_agent,
        )
        headers["x-openai-target-path"] = target_path
        headers["x-openai-target-route"] = target_path
        headers.pop("Content-Type", None)

        response = await self.session.get(
            f"{defaults.CHATGPT_BASE_URL}{target_path}", headers=headers
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"ChatGPT Web conversation GET returned HTTP {response.status_code}"
            )
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("ChatGPT Web conversation GET returned an invalid payload")

        current_node = payload.get("current_node")
        mapping = payload.get("mapping")
        node = mapping.get(current_node) if isinstance(mapping, dict) else None
        message = node.get("message") if isinstance(node, dict) else None
        if not isinstance(message, dict):
            return ConversationState()

        author = message.get("author")
        content = message.get("content")
        metadata = message.get("metadata")
        author = author if isinstance(author, dict) else {}
        content = content if isinstance(content, dict) else {}
        metadata = metadata if isinstance(metadata, dict) else {}
        finish_details = metadata.get("finish_details")
        finish_details = finish_details if isinstance(finish_details, dict) else {}
        parts = content.get("parts")
        text = "".join(part for part in parts if isinstance(part, str)) if isinstance(parts, list) else ""
        if not text and isinstance(content.get("text"), str):
            text = content["text"]

        matches_expected_message: bool | None = None
        if expected_user_message_id:
            matches_expected_message = False
            ancestor_id = current_node if isinstance(current_node, str) else None
            visited: set[str] = set()
            while ancestor_id and ancestor_id not in visited:
                if ancestor_id == expected_user_message_id:
                    matches_expected_message = True
                    break
                visited.add(ancestor_id)
                ancestor = mapping.get(ancestor_id) if isinstance(mapping, dict) else None
                ancestor_message = ancestor.get("message") if isinstance(ancestor, dict) else None
                ancestor_author = ancestor_message.get("author") if isinstance(ancestor_message, dict) else None
                if isinstance(ancestor_author, dict) and ancestor_author.get("role") == "user":
                    break
                parent_id = ancestor.get("parent") if isinstance(ancestor, dict) else None
                ancestor_id = parent_id if isinstance(parent_id, str) else None
        return ConversationState(
            node_id=current_node if isinstance(current_node, str) else None,
            parent_id=node.get("parent") if isinstance(node.get("parent"), str) else None,
            role=author.get("role"),
            recipient=message.get("recipient"),
            content_type=content.get("content_type"),
            message_status=message.get("status"),
            finish_type=finish_details.get("type"),
            text=text,
            matches_expected_message=matches_expected_message,
        )
