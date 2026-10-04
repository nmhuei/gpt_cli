"""Read the current upstream conversation node after local stream uncertainty."""

from __future__ import annotations

from dataclasses import dataclass
import json
import uuid

from curl_cffi.requests import AsyncSession

from ..config import defaults
from ..core.types import TokenBundle
from ..telemetry.events import TelemetryEvent
from ..telemetry.parser import SSETelemetryParser
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
    timeline: tuple[TelemetryEvent, ...] = ()


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

    @staticmethod
    def _branch_node_ids(
        mapping: dict[str, object], current_node: str, expected_user_message_id: str
    ) -> list[str]:
        """Return the expected user branch in chronological order, or nothing."""
        reverse_path: list[str] = []
        node_id: str | None = current_node
        visited: set[str] = set()
        while node_id and node_id not in visited:
            visited.add(node_id)
            reverse_path.append(node_id)
            if node_id == expected_user_message_id:
                return list(reversed(reverse_path))
            node = mapping.get(node_id)
            message = node.get("message") if isinstance(node, dict) else None
            author = message.get("author") if isinstance(message, dict) else None
            if isinstance(author, dict) and author.get("role") == "user":
                return []
            parent = node.get("parent") if isinstance(node, dict) else None
            node_id = parent if isinstance(parent, str) else None
        return []

    @staticmethod
    def _current_branch_node_ids(mapping: dict[str, object], current_node: str) -> list[str]:
        """Return the complete root-to-current branch for a newly-created turn.

        This is deliberately separate from ``_branch_node_ids``: ordinary
        recovery must prove that a fetched DAG belongs to its submitted user
        message, while a fresh image turn may have its client UUID replaced by
        the server before the image tool emits its asset node.
        """
        reverse_path: list[str] = []
        node_id: str | None = current_node
        visited: set[str] = set()
        while node_id and node_id not in visited:
            visited.add(node_id)
            reverse_path.append(node_id)
            node = mapping.get(node_id)
            parent = node.get("parent") if isinstance(node, dict) else None
            if parent == "client-created-root" or not isinstance(parent, str) or not parent:
                return list(reversed(reverse_path))
            node_id = parent
        return []

    @staticmethod
    def _message_text(content: dict[str, object]) -> str:
        parts = content.get("parts")
        if isinstance(parts, list):
            return "".join(part for part in parts if isinstance(part, str))
        value = content.get("text") or content.get("content")
        return value if isinstance(value, str) else ""

    @classmethod
    def _timeline_from_branch(
        cls, mapping: dict[str, object], node_ids: list[str]
    ) -> tuple[TelemetryEvent, ...]:
        """Normalize only server-exposed timeline items, never hidden reasoning."""
        events: list[TelemetryEvent] = []
        started: dict[str, str] = {}
        for node_id in node_ids:
            node = mapping.get(node_id)
            message = node.get("message") if isinstance(node, dict) else None
            if not isinstance(message, dict):
                continue
            author = message.get("author")
            author = author if isinstance(author, dict) else {}
            content = message.get("content")
            content = content if isinstance(content, dict) else {}
            metadata = message.get("metadata")
            metadata = metadata if isinstance(metadata, dict) else {}
            role = author.get("role")
            recipient = message.get("recipient")
            content_type = content.get("content_type")
            message_id = message.get("id") if isinstance(message.get("id"), str) else node_id

            if (
                role == "assistant"
                and isinstance(recipient, str)
                and recipient not in ("", "all", "final")
                and content_type != "thoughts"
            ):
                payload = cls._message_text(content)
                tool = recipient
                try:
                    parsed = json.loads(payload)
                    if isinstance(parsed, dict):
                        action = parsed.get("action") or parsed.get("name")
                        path = parsed.get("path")
                        if not action and isinstance(path, str):
                            action = path.rstrip("/").rsplit("/", 1)[-1]
                        if isinstance(action, str) and action:
                            tool = action
                except (TypeError, json.JSONDecodeError):
                    pass
                started[message_id] = tool
                events.append(TelemetryEvent("tool_call_started", {
                    "id": message_id,
                    "tool": tool,
                    "payload": payload,
                    "status": message.get("status"),
                    "source": "dag",
                }))
            elif role == "tool":
                parent = node.get("parent") if isinstance(node, dict) else None
                call_id = parent if isinstance(parent, str) else message_id
                invoked = metadata.get("invoked_plugin") or metadata.get("invoked_resource") or {}
                invoked = invoked if isinstance(invoked, dict) else {}
                tool = started.get(call_id) or author.get("name") or invoked.get("title") or "tool"
                events.append(TelemetryEvent("tool_call_finished", {
                    "id": call_id,
                    "tool": tool,
                    "title": metadata.get("reasoning_title"),
                    "resource": invoked,
                    "output": cls._message_text(content),
                    "source": "dag",
                }))
                events.extend(SSETelemetryParser()._image_artifact_events(message))
            elif content_type == "thoughts":
                thoughts = content.get("thoughts")
                if isinstance(thoughts, list) and thoughts:
                    events.append(TelemetryEvent("thinking", {
                        "thoughts": thoughts,
                        "title": metadata.get("reasoning_title"),
                        "source": "dag",
                    }))
            elif content_type == "reasoning_recap":
                recap = cls._message_text(content)
                if recap:
                    events.append(TelemetryEvent("reasoning_recap", {
                        "recap": recap,
                        "duration_sec": metadata.get("finished_duration_sec"),
                        "source": "dag",
                    }))
        return tuple(events)

    async def fetch(
        self,
        conversation_id: str,
        *,
        expected_user_message_id: str | None = None,
        allow_current_branch: bool = False,
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
        branch_node_ids: list[str] = []
        if expected_user_message_id:
            if isinstance(mapping, dict) and isinstance(current_node, str):
                branch_node_ids = self._branch_node_ids(
                    mapping, current_node, expected_user_message_id
                )
            matches_expected_message = bool(branch_node_ids)
        if (
            allow_current_branch
            and not branch_node_ids
            and isinstance(mapping, dict)
            and isinstance(current_node, str)
        ):
            branch_node_ids = self._current_branch_node_ids(mapping, current_node)
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
            timeline=self._timeline_from_branch(mapping, branch_node_ids) if isinstance(mapping, dict) else (),
        )

    async def fetch_settled(
        self,
        conversation_id: str,
        *,
        expected_user_message_id: str | None = None,
        allow_current_branch: bool = False,
        max_attempts: int = defaults.DEFAULT_RECOVERY_POLL_ATTEMPTS,
        interval_seconds: float = defaults.DEFAULT_RECOVERY_POLL_INTERVAL,
    ) -> ConversationState:
        """Poll the server-side DAG until the current turn settles on the backend.

        When the frontend SSE stream drops or is cut by Sentinel/Cloudflare,
        the backend inference worker continues computing and writing to the DAG.
        This polls the DAG until the node finishes, transitions to an active tool,
        or times out.
        """
        import asyncio

        last_state = ConversationState()
        for attempt in range(max_attempts):
            try:
                state = await self.fetch(
                    conversation_id,
                    expected_user_message_id=expected_user_message_id,
                    allow_current_branch=allow_current_branch,
                )
                last_state = state

                # If the state does not match our expected turn branch, stop
                if state.matches_expected_message is False:
                    return state

                # 1. Settled: Assistant completed terminal text response
                if (
                    state.role == "assistant"
                    and state.recipient in (None, "", "all", "final")
                    and state.content_type == "text"
                    and (
                        state.message_status in {"finished_successfully", "finished", "complete"}
                        or bool(state.finish_type)
                    )
                ):
                    return state

                # 2. Settled: Assistant transitioned to server-side tool execution
                if (
                    state.role == "assistant"
                    and state.recipient not in (None, "", "all", "final")
                ):
                    return state

                # 3. Definite failure on backend
                if state.message_status in {"failed", "error"}:
                    return state
            except Exception:
                pass

            if attempt < max_attempts - 1 and interval_seconds > 0:
                await asyncio.sleep(interval_seconds)

        return last_state

    async def fetch_until_image_artifact(
        self,
        conversation_id: str,
        *,
        expected_user_message_id: str | None = None,
        max_attempts: int = 30,
        interval_seconds: float = 2.0,
    ) -> ConversationState:
        """Wait for an image tool's asynchronous asset node in a fresh DAG.

        Image generation can acknowledge its tool call on SSE before its image
        file node is committed.  This is intentionally image-only: ordinary
        conversation recovery continues to stop as soon as its terminal node
        is available.
        """
        import asyncio

        last_state = ConversationState()
        for attempt in range(max_attempts):
            try:
                state = await self.fetch(
                    conversation_id,
                    expected_user_message_id=expected_user_message_id,
                    allow_current_branch=True,
                )
                last_state = state
                if any(event.event_type == "image_artifact" for event in state.timeline):
                    return state
                if state.message_status in {"failed", "error"}:
                    return state
            except Exception:
                pass
            if attempt < max_attempts - 1 and interval_seconds > 0:
                await asyncio.sleep(interval_seconds)
        return last_state
