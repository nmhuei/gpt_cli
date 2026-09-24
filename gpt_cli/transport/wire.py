import asyncio
import json
import uuid
import time
from typing import Any, Callable
from curl_cffi.requests import AsyncSession
from ..core.types import TokenBundle, TurnResult
from ..config import defaults
from ..models.registry import ModelRegistry
from .conduit import ConduitHandshake

class WireTransport:
    """High-performance Wire Conduit transport (0 MB browser RAM) via curl_cffi."""
    
    def __init__(self, bundle: TokenBundle, user_agent: str | None = None):
        self.bundle = bundle
        self.user_agent = user_agent or defaults.DEFAULT_USER_AGENT
        self.session = AsyncSession(impersonate="chrome146")

    def build_payload(
        self,
        prompt: str,
        conversation_id: str | None = None,
        parent_id: str | None = None,
        model_alias: str | None = None,
        effort: str | None = None
    ) -> dict[str, Any]:
        model_def = ModelRegistry.resolve(model_alias)
        chosen_effort = effort or model_def.default_effort

        msg_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "action": "next",
            "messages": [
                {
                    "id": msg_id,
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [prompt]},
                }
            ],
            "model": model_def.slug,
            "parent_message_id": parent_id or str(uuid.uuid4()),
            "conversation_mode": {"kind": "primary_assistant"},
        }
        if model_def.supports_thinking:
            payload["thinking_effort"] = chosen_effort
        if conversation_id:
            payload["conversation_id"] = conversation_id
        return payload

    async def _send_once(
        self,
        prompt: str,
        conversation_id: str | None,
        parent_id: str | None,
        model_alias: str | None,
        effort: str | None,
        on_event: Callable[[Any], None] | None,
    ) -> TurnResult:
        model_def = ModelRegistry.resolve(model_alias)
        t0 = time.perf_counter()

        # Step 1: Conduit Handshake
        headers, s_id, t_id, conduit = await ConduitHandshake.prepare_turn(
            self.session,
            self.bundle,
            model_slug=model_def.slug,
            user_agent=self.user_agent,
            conversation_id=conversation_id
        )

        # Step 2: Build Payload
        payload = self.build_payload(
            prompt=prompt,
            conversation_id=conversation_id,
            parent_id=parent_id,
            model_alias=model_alias,
            effort=effort,
        )

        # Step 3: Post Conversation Stream
        response = await self.session.post(
            defaults.FCONV_CONVERSATION_URL,
            headers=headers,
            json=payload,
            stream=True,
            timeout=defaults.DEFAULT_TIMEOUT_SECONDS
        )

        if response.status_code != 200:
            error_body = ""
            try:
                error_body = (await response.text())[:300]
            except Exception:
                pass
            raise RuntimeError(f"ChatGPT Web POST returned HTTP {response.status_code}: {error_body}")

        # Step 4: Stream SSE and Parse
        full_text = []
        captured_conv_id = conversation_id
        last_msg_id = parent_id or ""
        resolved_model = model_def.slug
        is_done = False

        from ..telemetry.parser import SSETelemetryParser
        parser = SSETelemetryParser()

        try:
            async for chunk in response.aiter_content():
                events = parser.feed(chunk)
                for ev in events:
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
                    elif ev.event_type == "done":
                        is_done = True
                if is_done:
                    break
        except Exception:
            # If server terminated socket after sending text or done signal, ignore premature close
            if not full_text:
                raise

        for ev in parser.finish():
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

        t1 = time.perf_counter()
        return TurnResult(
            text="".join(full_text),
            conversation_id=captured_conv_id or "",
            message_id=last_msg_id,
            model=resolved_model,
            duration_ms=int((t1 - t0) * 1000)
        )

    async def send_turn(
        self,
        prompt: str,
        conversation_id: str | None = None,
        parent_id: str | None = None,
        model_alias: str | None = None,
        effort: str | None = None,
        on_event: Callable[[Any], None] | None = None,
        max_retries: int = 2,
    ) -> TurnResult:
        last_err: Exception | None = None
        for attempt in range(max_retries):
            try:
                return await self._send_once(
                    prompt=prompt,
                    conversation_id=conversation_id,
                    parent_id=parent_id,
                    model_alias=model_alias,
                    effort=effort,
                    on_event=on_event,
                )
            except Exception as e:
                last_err = e
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    raise last_err from None
        raise last_err or RuntimeError("send_turn failed after retries")

    async def close(self):
        await self.session.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
