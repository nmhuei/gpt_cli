"""Own one live local session and serialize its initial and queued Wire turns."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.types import SteerContext, TokenBundle, TurnResult, is_completed_turn
from ..session.live_turns import LiveTurnRegistry, TurnOwner
from ..session.logger import SessionLogManager
from ..session.models import SessionRecord
from ..session.registry import SessionRegistry, SessionLookupError
from ..session.turn_control import TurnControlServer
from ..telemetry.events import TelemetryEvent
from ..telemetry.bqa_activity import BQAActivityStream
from ..config.settings import Settings
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
        capture_images: bool = False,
        clock: Callable[[], float] = time.monotonic,
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
        self.capture_images = capture_images
        self._clock = clock
        self._tool_started_at: dict[str, float] = {}
        self._owner: TurnOwner | None = None
        self._control: TurnControlServer | None = None
        self._transport: Any | None = None
        self._steer_context: SteerContext | None = None
        self._active_parent_id: str | None = None
        self._system_hints: list[str] = []
        self._selected_mcp_sources: list[dict[str, Any]] = []
        self._bqa_task: asyncio.Task[None] | None = None

    async def run_initial(
        self,
        prompt: str,
        *,
        system_hints: list[str] | None = None,
        selected_mcp_sources: list[dict[str, Any]] | None = None,
    ) -> TurnResult:
        self._system_hints = list(system_hints or [])
        self._selected_mcp_sources = list(selected_mcp_sources or [])
        raw_socket_path = Path(self.live_registry.runtime_dir) / f"{self.session.local_id}.sock"
        socket_path = TurnControlServer.resolve_socket_path(raw_socket_path, self.session.local_id)
        owner = TurnOwner.create_for_current_process(self.session.local_id, socket_path)
        self.live_registry.claim(owner)
        self._owner = owner
        try:
            # The session may have been deleted after CLI resolution but before live claim.
            # Keep this under cleanup so a failed revalidation releases the live owner.
            self.session = self.registry.require_session(self.session.local_id)
            self._control = TurnControlServer(owner, self.live_registry, self.request_steer)
            await self._control.start()
            async with self.transport_factory(self.token_bundle) as transport:
                self._transport = transport
                self._start_bqa_observer_if_bound()
                initial = await self._execute_turn(prompt)
                if initial.status not in {"completed", "terminal"}:
                    return initial
                for pending_prompt in self.registry.take_pending_steers(self.session.local_id):
                    self.live_registry.enqueue(
                        self.session.local_id,
                        request_id=uuid.uuid4().hex,
                        prompt=pending_prompt,
                    )
                latest_result = initial
                while True:
                    queued = self.live_registry.claim_next_queued(self.session.local_id)
                    if queued is None:
                        break
                    queued_result = await self._execute_turn(queued.prompt)
                    latest_result = queued_result
                    if queued_result.status not in {"completed", "terminal"}:
                        break
                    self.live_registry.complete_queued(self.session.local_id, queued.request_id)
                return latest_result
        finally:
            if self._bqa_task is not None:
                self._bqa_task.cancel()
                try:
                    await self._bqa_task
                except asyncio.CancelledError:
                    pass
                self._bqa_task = None
            self._transport = None
            if self._owner is not None:
                try:
                    prompts = self.live_registry.queued_prompts(self.session.local_id)
                except Exception:
                    prompts = []
                self.registry.preserve_pending_steers(
                    self.session.local_id,
                    prompts,
                )
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

    def _start_bqa_observer_if_bound(self) -> None:
        """Observe only a session-bound BQA workspace; never global audit data."""
        chat_id = self.session.bqa_chat_id
        if not chat_id or self._bqa_task is not None:
            return
        observer = BQAActivityStream(Settings().bqa_activity_url)
        self._bqa_task = asyncio.create_task(observer.run(chat_id, self._emit_bqa_event))

    def _emit_bqa_event(self, event: TelemetryEvent) -> None:
        # BQA events are journals only: no DAG pointer or completion mutations.
        self.log_manager.log_event(self.session.local_id, event, turn_index=self.session.turns + 1)
        if self.emit_event is not None:
            self.emit_event(event)

    async def _execute_turn(self, prompt: str) -> TurnResult:
        if self._transport is None:
            raise RuntimeError("Turn runner transport is not active")
        turn_number = self.session.turns + 1
        captured_thoughts: list[str] = []
        captured_tools: list[dict[str, Any]] = []
        recovery: dict[str, Any] | None = None
        self._steer_context = None
        self._active_parent_id = self.session.last_message_id
        self._tool_started_at = {}

        def on_event(event: TelemetryEvent) -> None:
            nonlocal recovery
            self.log_manager.log_event(self.session.local_id, event, turn_index=turn_number)
            if event.event_type == "tool_call_started":
                call_id = event.data.get("id")
                if isinstance(call_id, str) and call_id:
                    self._tool_started_at[call_id] = self._clock()
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
            elif event.event_type in {"thinking", "reasoning_recap"}:
                if event.event_type == "reasoning_recap":
                    recap = event.data.get("recap")
                    if recap and recap not in captured_thoughts:
                        captured_thoughts.append(recap)
                else:
                    for thought in event.data.get("thoughts", []):
                        summary = thought.get("summary") if isinstance(thought, dict) else str(thought)
                        if summary and summary not in captured_thoughts:
                            captured_thoughts.append(summary)
            elif event.event_type == "tool_call_started":
                captured_tools.append(dict(event.data))
            elif event.event_type == "tool_call_finished":
                call_id = event.data.get("id")
                matched_call: dict[str, Any] | None = None
                if isinstance(call_id, str) and call_id:
                    for candidate in reversed(captured_tools):
                        if candidate.get("id") == call_id:
                            matched_call = candidate
                            break
                elif len(captured_tools) == 1:
                    matched_call = captured_tools[0]
                if matched_call is None:
                    matched_call = {"id": call_id, "tool": event.data.get("tool", "tool")}
                    captured_tools.append(matched_call)
                matched_call["result"] = event.data.get("output")
                matched_call["result_title"] = event.data.get("title")
                matched_call["finished"] = True
                if isinstance(call_id, str) and call_id in self._tool_started_at:
                    elapsed_ms = round((self._clock() - self._tool_started_at.pop(call_id)) * 1000)
                    matched_call["client_elapsed_ms"] = elapsed_ms
                tool_name = str(event.data.get("tool") or matched_call.get("tool") or "")
                server_duration_ms = event.data.get("server_duration_ms")
                if isinstance(server_duration_ms, (int, float)) and not isinstance(server_duration_ms, bool) and server_duration_ms >= 0:
                    matched_call["server_duration_ms"] = server_duration_ms
                elif tool_name == "terminal_execute":
                    output = event.data.get("output")
                    try:
                        payload = json.loads(output) if isinstance(output, str) else output
                    except (json.JSONDecodeError, TypeError):
                        payload = None
                    duration_ms = payload.get("duration_ms") if isinstance(payload, dict) else None
                    if isinstance(duration_ms, (int, float)) and not isinstance(duration_ms, bool) and duration_ms >= 0:
                        matched_call["server_duration_ms"] = duration_ms
                if event.data.get("source"):
                    matched_call["source"] = event.data["source"]
                for timing_key in ("client_elapsed_ms", "server_duration_ms"):
                    if timing_key in matched_call:
                        event.data[timing_key] = matched_call[timing_key]
                if tool_name == "host_workspace_bind" or tool_name.endswith("workspace_bind"):
                    output = event.data.get("output")
                    try:
                        payload = json.loads(output) if isinstance(output, str) else output
                    except (json.JSONDecodeError, TypeError):
                        payload = None
                    if isinstance(payload, dict):
                        chat_id = payload.get("chat_id") or payload.get("session_id")
                        workspace_path = payload.get("workspace") or payload.get("workspace_dir")
                        if isinstance(chat_id, str) or isinstance(workspace_path, str):
                            self.registry.bind_workspace(
                                self.session.local_id,
                                chat_id=chat_id if isinstance(chat_id, str) else None,
                                workspace_path=workspace_path if isinstance(workspace_path, str) else None,
                            )
                            self.session = self.registry.require_session(self.session.local_id)
                            self._start_bqa_observer_if_bound()
            elif event.event_type == "recovery_started":
                recovery = dict(event.data)
            if self.emit_event is not None:
                self.emit_event(event)

        send_args = {
            "prompt": prompt,
            "conversation_id": self.session.conversation_id,
            "parent_id": self.session.last_message_id,
            "model_alias": self.model,
            "effort": self.effort,
            "on_event": on_event,
        }
        if self._system_hints:
            send_args["system_hints"] = self._system_hints
        if self._selected_mcp_sources:
            send_args["selected_mcp_sources"] = self._selected_mcp_sources
        if self.capture_images:
            send_args["capture_images"] = True
        try:
            result = await self._transport.send_turn(
                **send_args,
            )
        except asyncio.CancelledError:
            for tool_call in captured_tools:
                if not tool_call.get("finished"):
                    tool_call["status"] = "client_cancelled"
                    call_id = tool_call.get("id")
                    if isinstance(call_id, str) and call_id in self._tool_started_at:
                        tool_call["client_elapsed_ms"] = round((self._clock() - self._tool_started_at.pop(call_id)) * 1000)
            self.log_manager.log_turn(
                local_id=self.session.local_id,
                turn_index=turn_number,
                prompt=prompt,
                response="",
                model=self.model,
                duration_ms=0,
                conversation_id=self.session.conversation_id,
                message_id=self.session.last_message_id,
                thinking=captured_thoughts,
                tool_calls=captured_tools,
                recovery=recovery,
                error="client_cancelled",
            )
            raise
        final_conversation = result.conversation_id or self.session.conversation_id or ""
        final_message = result.message_id or self.session.last_message_id or ""
        if is_completed_turn(result):
            self.registry.update_turn(
                self.session.local_id,
                conversation_id=final_conversation,
                last_message_id=final_message,
                model=result.model,
            )
        else:
            self.registry.checkpoint(
                self.session.local_id,
                final_conversation,
                final_message,
                result.model,
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
            recovery=recovery,
            error=result.error,
        )
        return result
