import asyncio
import calendar
import json
import uuid
import time
from typing import Any, Callable
from curl_cffi.requests import AsyncSession
from ..core.types import SteerContext, TokenBundle, TurnResult
from ..config import defaults
from ..config.settings import Settings
from ..models.registry import ModelRegistry
from .conduit import ConduitHandshake
from .admission import UpstreamAdmission, UpstreamCooldownError, is_upstream_rejection

class WireTransport:
    """High-performance Wire Conduit transport (0 MB browser RAM) via curl_cffi."""
    
    def __init__(
        self,
        bundle: TokenBundle,
        user_agent: str | None = None,
        admission: UpstreamAdmission | None = None,
    ):
        self.bundle = bundle
        self.user_agent = user_agent or defaults.DEFAULT_USER_AGENT
        self.session = AsyncSession(impersonate="chrome146")
        self.admission = admission or UpstreamAdmission()
        self.latest_turn_headers: dict[str, str] = {}
        self.latest_turn_trace_id: str | None = None

    def build_payload(
        self,
        prompt: str,
        conversation_id: str | None = None,
        parent_id: str | None = None,
        model_alias: str | None = None,
        effort: str | None = None,
        system_hints: list[str] | None = None,
        selected_mcp_sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        model_def = ModelRegistry.resolve(model_alias)
        chosen_effort = effort or model_def.default_effort
        if chosen_effort not in ("extended", "standard"):
            chosen_effort = "standard" if chosen_effort in ("low", "none", "off", "min", "medium") else "extended"

        msg_id = str(uuid.uuid4())
        message: dict[str, Any] = {
            "id": msg_id,
            "author": {"role": "user"},
            "content": {"content_type": "text", "parts": [prompt]},
        }
        metadata: dict[str, Any] = {}
        if selected_mcp_sources:
            metadata["selected_mcp_sources"] = list(selected_mcp_sources)
        if model_def.supports_thinking:
            metadata["thinking_effort"] = chosen_effort
        if metadata:
            message["metadata"] = metadata
        payload: dict[str, Any] = {
            "action": "next",
            "messages": [message],
            "model": model_def.slug,
            "parent_message_id": parent_id or "client-created-root",
            "conversation_mode": {"kind": "primary_assistant"},
        }
        if model_def.supports_thinking:
            payload["thinking_effort"] = chosen_effort
        if system_hints:
            payload["system_hints"] = list(system_hints)
        if conversation_id and parent_id and parent_id != "client-created-root":
            payload["conversation_id"] = conversation_id
        else:
            payload["parent_message_id"] = "client-created-root"
        return payload

    def build_steer_payload(self, prompt: str, context: SteerContext) -> dict[str, Any]:
        """Build the observed native async-task steer request body."""
        if not context.async_task_id:
            raise ValueError("Native steer requires an async task id")
        timestamp = time.time()
        local_time = time.localtime(timestamp)
        timezone_offset = round((calendar.timegm(local_time) - int(timestamp)) / 60.0)
        return {
            "action": "next",
            "messages": [{
                "id": str(uuid.uuid4()),
                "author": {"role": "user"},
                "content": {"content_type": "text", "parts": [prompt]},
            }],
            "conversation_id": context.conversation_id,
            "parent_message_id": context.parent_message_id,
            "model": context.model,
            "client_prepare_state": context.client_prepare_state,
            "conversation_mode": {"kind": "primary_assistant"},
            "timezone": "Asia/Ho_Chi_Minh",
            "timezone_offset_min": timezone_offset,
            "supports_buffering": True,
            "supported_encodings": ["v1"],
            "async_task_id": context.async_task_id,
            "steer_turn_action": context.steer_turn_action,
            **({"async_task_turn_id": context.async_task_turn_id} if context.async_task_turn_id else {}),
        }

    async def send_steer(self, prompt: str, context: SteerContext) -> bool:
        """Submit native steer only for a parser-observed async upstream turn."""
        payload = self.build_steer_payload(prompt, context)
        headers = dict(context.headers)
        headers.update({
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-openai-target-path": "/backend-api/f/steer_turn",
            "x-openai-target-route": "/backend-api/f/steer_turn",
        })
        if context.turn_trace_id:
            headers["x-oai-turn-trace-id"] = context.turn_trace_id
        response = await self.session.post(
            defaults.FCONV_STEER_URL,
            headers=headers,
            json=payload,
            timeout=Settings().timeout_seconds,
        )
        return 200 <= response.status_code < 300

    async def _send_once(
        self,
        prompt: str,
        conversation_id: str | None = None,
        parent_id: str | None = None,
        model_alias: str | None = None,
        effort: str | None = None,
        system_hints: list[str] | None = None,
        selected_mcp_sources: list[dict[str, Any]] | None = None,
        on_event: Callable[[Any], None] | None = None,
        capture_images: bool = False,
    ) -> TurnResult:
        model_def = ModelRegistry.resolve(model_alias)
        t0 = time.perf_counter()

        chosen_effort = effort or model_def.default_effort
        if chosen_effort not in ("extended", "standard"):
            chosen_effort = "standard" if chosen_effort in ("low", "none", "off", "min", "medium") else "extended"
        thinking_effort = chosen_effort if model_def.supports_thinking else None

        # Step 1: Conduit Handshake
        headers, s_id, t_id, conduit = await ConduitHandshake.prepare_turn(
            self.session,
            self.bundle,
            model_slug=model_def.slug,
            user_agent=self.user_agent,
            conversation_id=conversation_id,
            parent_id=parent_id,
            system_hints=system_hints,
            thinking_effort=thinking_effort,
        )
        self.latest_turn_headers = dict(headers)
        self.latest_turn_trace_id = t_id

        # Step 2: Build Payload
        payload = self.build_payload(
            prompt=prompt,
            conversation_id=conversation_id,
            parent_id=parent_id,
            model_alias=model_alias,
            effort=effort,
            system_hints=system_hints,
            selected_mcp_sources=selected_mcp_sources,
        )
        request_message_id = payload["messages"][0]["id"]

        # Step 3: Post Conversation Stream
        timeout_sec = Settings().timeout_seconds
        response = await self.session.post(
            defaults.FCONV_CONVERSATION_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=timeout_sec
        )

        if response.status_code != 200:
            error_body = ""
            if hasattr(response, "aiter_content"):
                try:
                    chunks = []
                    async for chunk in response.aiter_content():
                        chunks.append(chunk)
                    raw_bytes = b"".join(chunks)
                    error_body = raw_bytes.decode("utf-8", errors="replace")[:500]
                except Exception:
                    pass
            if not error_body and hasattr(response, "text"):
                try:
                    error_body = (response.text or "")[:500]
                except Exception:
                    pass
            import logging
            logging.getLogger("gpt_cli.wire").error(
                f"POST {response.status_code}: {error_body} | conv_id={conversation_id} | parent_id={parent_id} | payload={json.dumps(payload)[:400]}"
            )
            raise RuntimeError(f"ChatGPT Web POST returned HTTP {response.status_code}: {error_body}")

        # Step 4: Stream SSE and Parse
        full_text = []
        captured_conv_id = conversation_id
        last_msg_id = parent_id or ""
        resolved_model = model_def.slug
        is_done = False
        completion_seen = False
        pending_tool_ids: set[str] = set()
        anonymous_tool_calls = 0
        stream_err: Exception | None = None
        observed_events: list[Any] = []
        image_assets: dict[str, dict[str, object]] = {}
        image_errors: list[str] = []
        dag_state = None
        recovery_client = None
        fresh_image_turn = capture_images and conversation_id is None

        from ..telemetry.parser import SSETelemetryParser
        parser = SSETelemetryParser()

        def consume_event(ev: Any) -> None:
            """Update turn state from protocol identities, not a one-tool boolean."""
            nonlocal captured_conv_id, last_msg_id, completion_seen, is_done, anonymous_tool_calls, stream_err
            observed_events.append(ev)
            if on_event:
                on_event(ev)
            if ev.event_type == "delta":
                text_chunk = ev.data.get("text", "")
                if text_chunk:
                    full_text.append(text_chunk)
            elif ev.event_type == "conversation_id":
                captured_conv_id = ev.data.get("conversation_id")
            elif ev.event_type == "message_id":
                last_msg_id = ev.data.get("message_id")
            elif ev.event_type == "tool_call_started":
                tool_id = ev.data.get("id")
                if isinstance(tool_id, str) and tool_id:
                    pending_tool_ids.add(tool_id)
                else:
                    anonymous_tool_calls += 1
            elif ev.event_type == "tool_call_finished":
                tool_id = ev.data.get("id")
                if isinstance(tool_id, str) and tool_id:
                    pending_tool_ids.discard(tool_id)
                elif anonymous_tool_calls:
                    anonymous_tool_calls -= 1
            elif ev.event_type == "image_artifact":
                pointer = ev.data.get("asset_pointer")
                if isinstance(pointer, str) and pointer:
                    image_assets[pointer] = dict(ev.data)
            elif ev.event_type == "error":
                stream_err = RuntimeError(f"Upstream SSE error: {ev.data.get('error')}")
            elif ev.event_type == "done":
                completion_seen = True
            if completion_seen and not pending_tool_ids and not anonymous_tool_calls:
                is_done = True

        def event_identity(ev: Any) -> tuple[object, ...] | None:
            """Return a stable identity for de-duplicating recovered telemetry.

            The upstream stream and the later DAG snapshot use the same message id
            for tool calls.  Textual reasoning items do not reliably carry an id,
            so they deliberately remain visible when recovered rather than risk
            suppressing a distinct server-exposed recap.
            """
            data = getattr(ev, "data", None)
            if not isinstance(data, dict):
                return None
            event_id = data.get("id")
            if isinstance(event_id, str) and event_id:
                return (getattr(ev, "event_type", None), event_id)
            return None

        def emit_recovered_timeline(timeline: object) -> None:
            """Backfill server-exposed events absent from an interrupted SSE feed."""
            if not isinstance(timeline, tuple) or not timeline:
                return
            from ..telemetry.events import TelemetryEvent

            seen = {
                identity for event in observed_events
                if (identity := event_identity(event)) is not None
            }
            consume_event(TelemetryEvent("recovery_started", {
                "source": "dag",
                "reason": "sse_interrupted",
            }))
            for event in timeline:
                identity = event_identity(event)
                if identity is not None and identity in seen:
                    continue
                consume_event(event)
                if identity is not None:
                    seen.add(identity)

        try:
            async for chunk in response.aiter_content():
                events = parser.feed(chunk)
                for ev in events:
                    consume_event(ev)
                if is_done:
                    break
        except Exception as exc:
            stream_err = exc

        for ev in parser.finish():
            consume_event(ev)

        lifecycle_status = None
        if captured_conv_id and (stream_err is not None or not is_done):
            from .conversation import ConversationRecovery
            from .lifecycle import classify_lifecycle

            recovery_client = ConversationRecovery(
                self.bundle, session=self.session, user_agent=self.user_agent
            )
            try:
                # Poll DAG until settled on the backend (inference worker may still be generating)
                dag_state = await recovery_client.fetch_settled(
                    captured_conv_id,
                    expected_user_message_id=request_message_id,
                    allow_current_branch=fresh_image_turn,
                )
                recovered_completed_answer = (
                    (stream_err is not None or not is_done)
                    and dag_state.matches_expected_message is True
                    and dag_state.role == "assistant"
                    and dag_state.recipient in (None, "", "all", "final")
                    and dag_state.content_type == "text"
                    and dag_state.message_status in {"finished_successfully", "finished", "complete"}
                    and bool(dag_state.finish_type)
                    and bool(dag_state.node_id)
                )
                if recovered_completed_answer:
                    emit_recovered_timeline(getattr(dag_state, "timeline", ()))
                    full_text = [dag_state.text]
                    last_msg_id = dag_state.node_id
                    stream_err = None
                    lifecycle_status = "completed_turn"
                else:
                    for event in getattr(dag_state, "timeline", ()):
                        if getattr(event, "event_type", None) == "image_artifact":
                            consume_event(event)
                    lifecycle_status = classify_lifecycle(observed_events, dag_state).value
            except Exception as recovery_error:
                if stream_err is None:
                    stream_err = recovery_error

        # Some image artifacts are present only in the settled conversation DAG,
        # even when the SSE turn itself completed cleanly. Fetch that DAG only for
        # the dedicated image command to avoid adding a request to normal chat.
        if capture_images and not image_assets and captured_conv_id:
            try:
                if recovery_client is None:
                    from .conversation import ConversationRecovery

                    recovery_client = ConversationRecovery(
                        self.bundle, session=self.session, user_agent=self.user_agent
                    )
                if fresh_image_turn:
                    dag_state = await recovery_client.fetch_until_image_artifact(
                        captured_conv_id,
                        expected_user_message_id=request_message_id,
                    )
                elif dag_state is None:
                    dag_state = await recovery_client.fetch(
                        captured_conv_id,
                        expected_user_message_id=request_message_id,
                    )
                if dag_state.matches_expected_message is True or fresh_image_turn:
                    for event in dag_state.timeline:
                        if event.event_type == "image_artifact":
                            consume_event(event)
            except Exception as artifact_error:
                image_errors.append(f"Could not inspect the conversation for image artifacts: {artifact_error}")

        if image_assets:
            from .image_assets import download_image_asset

            image_paths: list[str] = []
            for asset in image_assets.values():
                try:
                    image_path = await download_image_asset(
                        self.session,
                        self.bundle,
                        asset,
                        user_agent=self.user_agent,
                    )
                    image_paths.append(str(image_path))
                except Exception as download_error:
                    image_errors.append(str(download_error))
        else:
            image_paths = []

        if lifecycle_status is None:
            from .lifecycle import classify_lifecycle
            lifecycle_status = classify_lifecycle(observed_events, None).value

        t1 = time.perf_counter()
        if lifecycle_status == "completed_turn" and full_text:
            turn_status = "completed"
        elif lifecycle_status == "model_terminal":
            turn_status = "terminal"
        elif lifecycle_status in {
            "connector_unavailable",
            "server_active",
            "tool_timeout",
            "transport_lost",
        }:
            turn_status = "incomplete"
        elif full_text:
            turn_status = "incomplete"
        else:
            turn_status = "failed"

        final_msg_id = last_msg_id if (full_text or last_msg_id) else (parent_id or "")

        return TurnResult(
            text="".join(full_text),
            conversation_id=captured_conv_id or "",
            message_id=final_msg_id,
            model=resolved_model,
            duration_ms=int((t1 - t0) * 1000),
            status=turn_status,
            error=str(stream_err) if stream_err else None,
            lifecycle_status=lifecycle_status,
            image_paths=image_paths,
            image_errors=image_errors,
        )

    async def send_turn(
        self,
        prompt: str,
        conversation_id: str | None = None,
        parent_id: str | None = None,
        model_alias: str | None = None,
        effort: str | None = None,
        system_hints: list[str] | None = None,
        selected_mcp_sources: list[dict[str, Any]] | None = None,
        on_event: Callable[[Any], None] | None = None,
        max_retries: int = 5,
        capture_images: bool = False,
    ) -> TurnResult:
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                delay = self.admission.reserve()
            except UpstreamCooldownError as exc:
                return TurnResult(
                    text="",
                    conversation_id=conversation_id or "",
                    message_id=parent_id or "",
                    model=ModelRegistry.resolve(model_alias).slug,
                    duration_ms=0,
                    status="failed",
                    error=str(exc),
                    lifecycle_status="upstream_error",
                )
            if delay:
                await asyncio.sleep(delay)
            tool_dispatched = False

            def observe_event(event: Any) -> None:
                """Forward telemetry while remembering whether replay became unsafe."""
                nonlocal tool_dispatched
                if event.event_type == "tool_call_started":
                    tool_dispatched = True
                if on_event:
                    on_event(event)

            try:
                send_args = dict(
                    prompt=prompt,
                    conversation_id=conversation_id,
                    parent_id=parent_id,
                    model_alias=model_alias,
                    effort=effort,
                    system_hints=system_hints,
                    selected_mcp_sources=selected_mcp_sources,
                    on_event=observe_event,
                )
                if capture_images:
                    send_args["capture_images"] = True
                res = await self._send_once(**send_args)
                if is_upstream_rejection(res.error):
                    self.admission.record_rejection(res.error)
                # If incomplete, stream has already delivered partial progress; do not auto-replay prompt
                return res
            except Exception as e:
                last_err = e
                # A tool may already have mutated external state. Replaying the
                # user prompt would duplicate that work and corrupt turn order.
                if tool_dispatched:
                    raise
                err_msg = str(e)
                if is_upstream_rejection(err_msg):
                    self.admission.record_rejection(err_msg)
                    raise
                # Interruption 1: 401 Unauthorized / 403 Forbidden (Token expired / auth failure)
                if any(x in err_msg for x in ("HTTP 401", "HTTP 403", "token_expired")):
                    from ..core.token_cache import TokenCacheManager
                    mgr = TokenCacheManager()
                    # Only trigger browser lifebuoy if JWT is actually missing or expired (Rule 1)
                    if not self.bundle.access_token or mgr.is_token_expired(self.bundle.access_token):
                        try:
                            from .lifebuoy import BrowserLifebuoy
                            lifebuoy = BrowserLifebuoy(mgr)
                            self.bundle = await lifebuoy.remint_credentials(headless=True)
                            try:
                                await self.session.close()
                            except Exception:
                                pass
                            self.session = AsyncSession(impersonate="chrome146")
                            await asyncio.sleep(1)
                            continue
                        except Exception:
                            pass

                # Interruption 2: 404 / 422 (DAG desync / missing node or conversation)
                if conversation_id and any(x in err_msg for x in ("HTTP 404", "HTTP 422", "conversation_not_found", "parent_message_not_found")):
                    try:
                        h = {"Authorization": f"Bearer {self.bundle.access_token}", "Accept": "application/json"}
                        if self.bundle.chatgpt_account_id:
                            h["ChatGPT-Account-ID"] = self.bundle.chatgpt_account_id
                        r = await self.session.get(f"{defaults.CHATGPT_BASE_URL}/backend-api/conversation/{conversation_id}", headers=h)
                        if r.status_code == 200:
                            cdata = r.json()
                            curr_node = cdata.get("current_node")
                            if curr_node and curr_node != parent_id:
                                parent_id = curr_node
                                continue
                    except Exception:
                        pass
                    conversation_id = None
                    parent_id = None
                    continue

                # Interruption 3: Network drop, connection reset, timeout, HTTP 500/502/503/504
                if attempt < max_retries - 1:
                    try:
                        await self.session.close()
                    except Exception:
                        pass
                    self.session = AsyncSession(impersonate="chrome146")
                    backoff = min(2 ** (attempt + 1), 20)
                    await asyncio.sleep(backoff)
                else:
                    raise last_err from None
        raise last_err or RuntimeError("send_turn failed after retries")

    async def close(self):
        await self.session.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
