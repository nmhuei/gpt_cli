"""Own one live local session and serialize its initial and queued Wire turns."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.types import SteerContext, TokenBundle, TurnResult
from ..session.live_turns import LiveTurnRegistry, TurnOwner
from ..session.logger import SessionLogManager
from ..session.models import SessionRecord
from ..session.registry import SessionRegistry
from ..session.turn_control import TurnControlServer
from ..telemetry.events import TelemetryEvent
from ..transport.wire import WireTransport


EventSink = Callable[[TelemetryEvent], None]


class SessionTurnRunner:
    def __init__(
        self,
        *,
        registry: SessionRegistry,
        live_registry: LiveTurnRegistry,
        log_manager: SessionLogManager,
        token_bundle: TokenBundle,
        session: SessionRecord,
        model: str,
        effort: str,
        transport_factory: Callable[[TokenBundle], Any] = WireTransport,
        emit_event: EventSink | None = None,
    ):
        self.registry = registry
        self.live_registry = live_registry
        self.log_manager = log_manager
        self.token_bundle = token_bundle
        self.session = session
        self.model = model
        self.effort = effort
        self.transport_factory = transport_factory
        self.emit_event = emit_event
        self._owner: TurnOwner | None = None
        self._control: TurnControlServer | None = None
        self._transport: Any | None = None
        self._steer_context: SteerContext | None = None
        self._active_parent_id: str | None = None

    async def run_initial(self, prompt: str) -> TurnResult:
        socket_path = Path(self.live_registry.runtime_dir) / f"{self.session.local_id}.sock"
        owner = TurnOwner.create_for_current_process(self.session.local_id, socket_path)
        self.live_registry.claim(owner)
        self._owner = owner
        self._control = TurnControlServer(owner, self.live_registry, self.request_steer)
        await self._control.start()
        try:
            async with self.transport_factory(self.token_bundle) as transport:
                self._transport = transport
                initial = await self._execute_turn(prompt)
                if initial.status not in {"completed", "terminal"}:
                    return initial
                while True:
                    queued = self.live_registry.claim_next_queued(self.session.local_id)
                    if queued is None:
                        break
                    queued_result = await self._execute_turn(queued.prompt)
                    if queued_result.status not in {"completed", "terminal"}:
                        break
                    self.live_registry.complete_queued(self.session.local_id, queued.request_id)
                return initial
        finally:
            self._transport = None
            if self._control is not None:
                await self._control.close()
                self._control = None
            self.live_registry.release(self.session.local_id, owner.owner_id)
            self._owner = None

    async def request_steer(self, request_id: str, prompt: str) -> dict[str, str]:
        """Native-steer an observed async task or durably queue a normal turn."""
        if self._owner is None or self._transport is None:
            return {"status": "rejected", "session_id": self.session.local_id}
        if self._steer_context is not None:
            try:
                if await self._transport.send_steer(prompt, self._steer_context):
                    return {"status": "steered", "session_id": self.session.local_id}
            except Exception:
                # A still-live owner can preserve the instruction in its durable queue.
                pass
        self.live_registry.enqueue(self.session.local_id, request_id=request_id, prompt=prompt)
        return {"status": "queued", "session_id": self.session.local_id}

    async def _execute_turn(self, prompt: str) -> TurnResult:
        if self._transport is None:
            raise RuntimeError("Turn runner transport is not active")
        turn_number = self.session.turns + 1
        captured_thoughts: list[str] = []
        captured_tools: list[dict[str, Any]] = []
        self._steer_context = None
        self._active_parent_id = self.session.last_message_id

        def on_event(event: TelemetryEvent) -> None:
            self.log_manager.log_event(self.session.local_id, event, turn_index=turn_number)
            if event.event_type == "conversation_id":
                conversation_id = event.data.get("conversation_id")
                if isinstance(conversation_id, str) and conversation_id:
                    self.registry.checkpoint(
                        self.session.local_id, conversation_id, self.session.last_message_id, self.model
                    )
                    self.session = self.registry.require_session(self.session.local_id)
                    self.live_registry.update_upstream(
                        self.session.local_id, conversation_id=conversation_id, model=self.model
                    )
            elif event.event_type == "message_id":
                message_id = event.data.get("message_id")
                if isinstance(message_id, str) and message_id:
                    self.registry.checkpoint(
                        self.session.local_id, self.session.conversation_id, message_id, self.model
                    )
                    self.session = self.registry.require_session(self.session.local_id)
                    self.live_registry.update_upstream(
                        self.session.local_id, parent_message_id=message_id, model=self.model
                    )
            elif event.event_type == "async_task":
                async_task_id = event.data.get("async_task_id")
                if isinstance(async_task_id, str) and async_task_id:
                    headers = dict(getattr(self._transport, "latest_turn_headers", {}))
                    trace_id = getattr(self._transport, "latest_turn_trace_id", None)
                    self._steer_context = SteerContext(
                        conversation_id=self.session.conversation_id or "",
                        parent_message_id=self._active_parent_id or "client-created-root",
                        model=self.model,
                        async_task_id=async_task_id,
                        async_task_turn_id=event.data.get("async_task_turn_id"),
                        headers=headers,
                        turn_trace_id=trace_id,
                    )
                    self.live_registry.update_upstream(
                        self.session.local_id,
                        async_task_id=async_task_id,
                        async_task_turn_id=event.data.get("async_task_turn_id"),
                        turn_trace_id=trace_id,
                    )
            elif event.event_type == "thinking":
                for thought in event.data.get("thoughts", []):
                    summary = thought.get("summary") if isinstance(thought, dict) else str(thought)
                    if summary:
                        captured_thoughts.append(summary)
            elif event.event_type == "tool_call_started":
                captured_tools.append(dict(event.data))
            if self.emit_event is not None:
                self.emit_event(event)

        result = await self._transport.send_turn(
            prompt=prompt,
            conversation_id=self.session.conversation_id,
            parent_id=self.session.last_message_id,
            model_alias=self.model,
            effort=self.effort,
            on_event=on_event,
        )
        final_conversation = result.conversation_id or self.session.conversation_id or ""
        final_message = result.message_id or self.session.last_message_id or ""
        self.registry.checkpoint(self.session.local_id, final_conversation, final_message, result.model)
        self.registry.update_turn(
            self.session.local_id,
            conversation_id=final_conversation,
            last_message_id=final_message,
            model=result.model,
        )
        self.session = self.registry.require_session(self.session.local_id)
        self.live_registry.update_upstream(
            self.session.local_id,
            conversation_id=final_conversation,
            parent_message_id=final_message,
            model=result.model,
        )
        self.log_manager.log_turn(
            local_id=self.session.local_id,
            turn_index=turn_number,
            prompt=prompt,
            response=result.text,
            model=result.model,
            duration_ms=result.duration_ms,
            conversation_id=final_conversation,
            message_id=final_message,
            thinking=captured_thoughts,
            tool_calls=captured_tools,
            error=result.error,
        )
        return result
