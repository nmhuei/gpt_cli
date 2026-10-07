import asyncio
import json
import logging
import time
import uuid
from typing import Any
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from ..config.settings import Settings
from ..core.bridge import resolve_prompt_with_bridge
from ..core.token_cache import TokenCacheManager
from ..core.types import is_completed_turn
from ..models.registry import ModelRegistry
from ..pool.worker_pool import WorkerPool
from ..session.registry import SessionRegistry
from ..telemetry.formatter import format_tool_call, format_tool_output
from ..transport.wire import WireTransport
from .gateway import SSEQueueOverflow, _safe_queue_put, _validate_gateway_messages
from .message_utils import _extract_content
from .prompt_formatter import format_chat_messages
from .session_title import _generate_local_session_title, _is_session_title_request
from .sse_utils import _finish_sse_worker
from .tool_utils import _normalize_tool_payload, _resolve_tool_name

logger = logging.getLogger("gpt_cli.server")

async def anthropic_messages(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}}, status_code=400)

    body, error = _validate_gateway_messages(body, anthropic=True)
    if error:
        return error
    messages = body["messages"]

    system_text = _extract_content(body.get("system", ""))
    settings = Settings()
    model_alias = body.get("model", settings.default_model)
    effort = body.get("effort", settings.default_effort)
    is_stream = bool(body.get("stream", False))

    if _is_session_title_request(messages, system_text):
        title = _generate_local_session_title(messages)
        if is_stream:
            async def title_stream():
                msg_id = f"msg_title_{uuid.uuid4().hex[:12]}"
                yield f"event: message_start\ndata: {json.dumps({'type': 'message_start', 'message': {'id': msg_id, 'type': 'message', 'role': 'assistant', 'content': [], 'model': model_alias, 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 5}}})}\n\n"
                yield f"event: content_block_start\ndata: {json.dumps({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}})}\n\n"
                yield f"event: content_block_delta\ndata: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': title}})}\n\n"
                yield f"event: content_block_stop\ndata: {json.dumps({'type': 'content_block_stop', 'index': 0})}\n\n"
                yield f"event: message_delta\ndata: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 5}})}\n\n"
                yield f"event: message_stop\ndata: {json.dumps({'type': 'message_stop'})}\n\n"
            return StreamingResponse(title_stream(), media_type="text/event-stream")
        return JSONResponse({
            "id": f"msg_title_{uuid.uuid4().hex[:12]}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": title}],
            "model": model_alias,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5}
        })

    tools = body.get("tools", [])
    registered_tools = {t.get("name") for t in tools if isinstance(t, dict) and t.get("name")}
    header_keys = {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "cookie", "x-api-key")}
    print(f"[GATEWAY] POST /v1/messages: msgs={len(messages)}, registered_tools={list(registered_tools)}, system_len={len(system_text)}, headers={header_keys}", flush=True)

    metadata = body.get("metadata") or {}
    purpose = str(metadata.get("purpose") or body.get("purpose") or "").lower()
    is_compaction = (purpose == "compaction")

    session_key = None
    if not is_compaction:
        session_key = (
            request.headers.get("x-deepseek-harness-session-id")
            or request.headers.get("x-claude-code-session-id")
            or request.headers.get("x-session-id")
            or request.headers.get("x-conversation-id")
            or metadata.get("session_id")
        )
    sess = None
    conv_id = None
    parent_id = None
    if session_key:
        reg = SessionRegistry()
        clean_key = str(session_key).replace("-", "_")
        sess_id = clean_key if clean_key.startswith("sess_") else f"sess_{clean_key}"
        sess = reg.get_or_create(session_id=sess_id, set_active=False)
        conv_id = sess.conversation_id
        parent_id = sess.last_message_id

    if conv_id and parent_id and len(messages) > 1:
        # Prefix Diffing: Extract all messages since the last assistant message
        last_asst_idx = max((i for i, m in enumerate(messages) if m.get("role") == "assistant"), default=-1)
        delta_messages = messages[last_asst_idx + 1:] if last_asst_idx >= 0 else messages[-1:]
        if not delta_messages:
            delta_messages = messages[-1:]
        prompt = format_chat_messages(delta_messages, tools=tools)
    else:
        conv_id = None
        parent_id = None
        prompt = format_chat_messages(messages, system_prompt=system_text, tools=tools)

    active_bridge = None if registered_tools else settings.default_bridge_name
    prompt = resolve_prompt_with_bridge(prompt, active_bridge)

    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": "No valid token bundle"}}, status_code=500)

    pool: WorkerPool = request.app.state.pool
    msg_id = f"msg_{uuid.uuid4().hex[:20]}"

    if not is_stream:
        captured_thinking: list[str] = []
        captured_tools: list[dict[str, Any]] = []
        captured_conv_id = conv_id
        captured_msg_id = parent_id

        def on_sync_event(ev):
            nonlocal captured_conv_id, captured_msg_id
            if ev.event_type == "thinking":
                for th in ev.data.get("thoughts", []):
                    s = th.get("summary") if isinstance(th, dict) else str(th)
                    if s and s not in captured_thinking:
                        captured_thinking.append(s)
                title = ev.data.get("title")
                if title and title not in captured_thinking:
                    captured_thinking.append(title)
            elif ev.event_type == "reasoning_recap":
                recap = ev.data.get("recap")
                if recap and recap not in captured_thinking:
                    captured_thinking.append(recap)
            elif ev.event_type == "tool_call_started":
                call_id = ev.data.get("id") or ev.data.get("tool")
                existing = next((t for t in captured_tools if (t.get("id") or t.get("tool")) == call_id), None)
                if existing:
                    existing.update(ev.data)
                else:
                    captured_tools.append(dict(ev.data))
            elif ev.event_type == "conversation_id":
                captured_conv_id = ev.data.get("conversation_id")
            elif ev.event_type == "message_id":
                captured_msg_id = ev.data.get("message_id")

        async def do_turn():
            async with WireTransport(bundle) as transport:
                return await transport.send_turn(
                    prompt=prompt,
                    conversation_id=conv_id,
                    parent_id=parent_id,
                    model_alias=model_alias,
                    effort=effort,
                    on_event=on_sync_event
                )

        try:
            result = await pool.execute(do_turn)
        except Exception as exc:
            if sess and not is_compaction and captured_conv_id and captured_msg_id:
                reg.checkpoint(
                    sess.local_id,
                    conversation_id=captured_conv_id,
                    message_id=captured_msg_id,
                    model=model_alias,
                )
            logger.exception("Error executing Anthropic message")
            return JSONResponse(
                {"type": "error", "error": {"type": "api_error", "message": str(exc)}},
                status_code=500,
            )
        if sess and not is_compaction:
            final_c = captured_conv_id or result.conversation_id
            final_m = captured_msg_id or result.message_id
            if final_c and final_m:
                if is_completed_turn(result):
                    reg.update_turn(sess.local_id, conversation_id=final_c, last_message_id=final_m, model=model_alias)
                else:
                    reg.checkpoint(sess.local_id, conversation_id=final_c, message_id=final_m, model=model_alias)

        if not is_completed_turn(result):
            return JSONResponse(
                {"type": "error", "error": {"type": "api_error", "message": result.error or "Turn did not complete"}},
                status_code=500,
            )

        content_blocks = []
        if captured_thinking:
            content_blocks.append({
                "type": "thinking",
                "thinking": "\n".join(captured_thinking) + "\n"
            })
        for tool in captured_tools:
            tool_name = tool.get("tool", "tool")
            resolved_name, should_unwrap = _resolve_tool_name(tool_name, registered_tools)
            raw_p = tool.get("payload", "{}")
            p_obj = _normalize_tool_payload(resolved_name, raw_p, should_unwrap)
            content_blocks.append({
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:20]}",
                "name": resolved_name,
                "input": p_obj
            })
        if result.text:
            content_blocks.append({"type": "text", "text": result.text})
        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        has_tool = bool(captured_tools)
        has_text = bool(result.text.strip())
        stop_reason = "tool_use" if (has_tool and not has_text) else "end_turn"

        return JSONResponse({
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": model_alias,
            "content": content_blocks,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": len(prompt.split()),
                "output_tokens": len(result.text.split())
            }
        })

    # Streaming Anthropic events
    async def event_generator():
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue(maxsize=100)

        _safe_queue_put(queue, ("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": model_alias,
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": len(prompt.split()), "output_tokens": 0}
            }
        }))

        current_block_index = -1
        current_block_type: str | None = None
        last_block_type: str | None = None
        seen_thoughts: set[str] = set()
        active_tool_id: str | None = None
        active_tool_name: str | None = None
        active_tool_payload: Any = None
        has_tool_call = False
        has_text_emitted = False
        out_tokens = 0
        captured_conv_id = conv_id
        captured_msg_id = parent_id

        def close_current_block():
            nonlocal current_block_type, current_block_index, active_tool_id, active_tool_name, active_tool_payload
            if current_block_type == "tool_use":
                resolved_name, should_unwrap = _resolve_tool_name(active_tool_name or "tool", registered_tools)
                args_obj = _normalize_tool_payload(resolved_name, active_tool_payload, should_unwrap)
                print(f"[GATEWAY-TOOL] resolved={resolved_name}, active_name={active_tool_name}, args={args_obj}", flush=True)
                _safe_queue_put(queue, ("content_block_delta", {
                    "type": "content_block_delta",
                    "index": current_block_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(args_obj)
                    }
                }))
                _safe_queue_put(queue, ("content_block_stop", {
                    "type": "content_block_stop",
                    "index": current_block_index
                }))
                current_block_type = None
                active_tool_id = None
                active_tool_name = None
                active_tool_payload = None
            elif current_block_type is not None:
                _safe_queue_put(queue, ("content_block_stop", {
                    "type": "content_block_stop",
                    "index": current_block_index
                }))
                current_block_type = None

        def on_event(ev):
            nonlocal out_tokens, current_block_index, current_block_type, last_block_type
            nonlocal active_tool_id, active_tool_name, active_tool_payload
            nonlocal has_tool_call, has_text_emitted
            nonlocal captured_conv_id, captured_msg_id

            if ev.event_type == "thinking":
                new_thoughts: list[str] = []
                for th in ev.data.get("thoughts", []):
                    s = th.get("summary") if isinstance(th, dict) else str(th)
                    if s and s not in seen_thoughts:
                        seen_thoughts.add(s)
                        new_thoughts.append(s)
                title = ev.data.get("title")
                if title and title not in seen_thoughts:
                    seen_thoughts.add(title)
                    new_thoughts.append(title)

                if new_thoughts:
                    th_text = "\n".join(new_thoughts) + "\n"
                    out_tokens += len(th_text.split())
                    if current_block_type != "thinking":
                        close_current_block()
                        current_block_index += 1
                        current_block_type = "thinking"
                        last_block_type = "thinking"
                        _safe_queue_put(queue, ("content_block_start", {
                            "type": "content_block_start",
                            "index": current_block_index,
                            "content_block": {"type": "thinking", "thinking": ""}
                        }))
                    _safe_queue_put(queue, ("content_block_delta", {
                        "type": "content_block_delta",
                        "index": current_block_index,
                        "delta": {"type": "thinking_delta", "thinking": th_text}
                    }))

            elif ev.event_type == "reasoning_recap":
                recap = ev.data.get("recap")
                if recap and recap not in seen_thoughts:
                    seen_thoughts.add(recap)
                    th_text = f"[{recap}]\n"
                    out_tokens += len(th_text.split())
                    if current_block_type != "thinking":
                        close_current_block()
                        current_block_index += 1
                        current_block_type = "thinking"
                        last_block_type = "thinking"
                        _safe_queue_put(queue, ("content_block_start", {
                            "type": "content_block_start",
                            "index": current_block_index,
                            "content_block": {"type": "thinking", "thinking": ""}
                        }))
                    _safe_queue_put(queue, ("content_block_delta", {
                        "type": "content_block_delta",
                        "index": current_block_index,
                        "delta": {"type": "thinking_delta", "thinking": th_text}
                    }))

            elif ev.event_type == "tool_call_started":
                call_id = ev.data.get("id") or ev.data.get("tool", "tool")
                new_payload = ev.data.get("payload")
                if current_block_type == "tool_use" and active_tool_id == call_id:
                    if new_payload:
                        active_tool_payload = new_payload
                else:
                    close_current_block()
                    has_tool_call = True
                    current_block_index += 1
                    current_block_type = "tool_use"
                    last_block_type = "tool_use"
                    active_tool_id = call_id
                    active_tool_name = ev.data.get("tool", "tool")
                    resolved_name, _ = _resolve_tool_name(active_tool_name, registered_tools)
                    active_tool_payload = new_payload or "{}"
                    tool_use_id = f"toolu_{uuid.uuid4().hex[:20]}"
                    _safe_queue_put(queue, ("content_block_start", {
                        "type": "content_block_start",
                        "index": current_block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": resolved_name,
                            "input": {}
                        }
                    }))

            elif ev.event_type == "tool_call_finished":
                if current_block_type == "tool_use":
                    new_payload = ev.data.get("payload")
                    if new_payload:
                        active_tool_payload = new_payload
                    close_current_block()

            elif ev.event_type == "delta":
                text = ev.data.get("text", "")
                if text:
                    has_text_emitted = True
                    out_tokens += len(text.split())
                    if current_block_type != "text":
                        close_current_block()
                        current_block_index += 1
                        current_block_type = "text"
                        last_block_type = "text"
                        _safe_queue_put(queue, ("content_block_start", {
                            "type": "content_block_start",
                            "index": current_block_index,
                            "content_block": {"type": "text", "text": ""}
                        }))
                    _safe_queue_put(queue, ("content_block_delta", {
                        "type": "content_block_delta",
                        "index": current_block_index,
                        "delta": {"type": "text_delta", "text": text}
                    }))

            elif ev.event_type == "conversation_id":
                captured_conv_id = ev.data.get("conversation_id")
            elif ev.event_type == "message_id":
                captured_msg_id = ev.data.get("message_id")

            elif ev.event_type == "done":
                close_current_block()
                if current_block_index == -1:
                    current_block_index = 0
                    _safe_queue_put(queue, ("content_block_start", {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""}
                    }))
                    _safe_queue_put(queue, ("content_block_stop", {
                        "type": "content_block_stop",
                        "index": 0
                    }))

                stop_reason = "tool_use" if last_block_type == "tool_use" else "end_turn"
                _safe_queue_put(queue, ("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": out_tokens}
                }))
                _safe_queue_put(queue, ("message_stop", {"type": "message_stop"}))

        overflowed = False
        worker_error: Exception | None = None

        async def worker():
            nonlocal overflowed, worker_error
            try:
                async with WireTransport(bundle) as transport:
                    turn_res = await transport.send_turn(
                        prompt=prompt,
                        conversation_id=conv_id,
                        parent_id=parent_id,
                        model_alias=model_alias,
                        effort=effort,
                        on_event=on_event
                    )
                    if sess and not is_compaction:
                        final_c = captured_conv_id or turn_res.conversation_id
                        final_m = captured_msg_id or turn_res.message_id
                        if final_c and final_m:
                            if is_completed_turn(turn_res):
                                reg.update_turn(sess.local_id, conversation_id=final_c, last_message_id=final_m, model=model_alias)
                            else:
                                reg.checkpoint(sess.local_id, conversation_id=final_c, message_id=final_m, model=model_alias)
                    if not is_completed_turn(turn_res):
                        worker_error = RuntimeError(turn_res.error or "Turn did not complete")
            except SSEQueueOverflow:
                overflowed = True
                if sess and not is_compaction and captured_conv_id and captured_msg_id:
                    reg.checkpoint(sess.local_id, conversation_id=captured_conv_id, message_id=captured_msg_id, model=model_alias)
            except asyncio.CancelledError:
                if sess and not is_compaction:
                    if captured_conv_id and captured_msg_id:
                        reg.checkpoint(sess.local_id, conversation_id=captured_conv_id, message_id=captured_msg_id, model=model_alias)
                raise
            except Exception as exc:
                worker_error = exc
                logger.exception("Error in Anthropic stream worker")
                if sess and not is_compaction and captured_conv_id and captured_msg_id:
                    reg.checkpoint(sess.local_id, conversation_id=captured_conv_id, message_id=captured_msg_id, model=model_alias)
            finally:
                await _finish_sse_worker(queue)

        task = asyncio.create_task(pool.execute(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_name, data = item
                yield f"event: {event_name}\ndata: {json.dumps(data)}\n\n"
            if overflowed:
                error_data = json.dumps({
                    "type": "error",
                    "error": {"type": "overloaded_error", "message": "SSE queue overflow"},
                })
                yield f"event: error\ndata: {error_data}\n\n"
                return
            if worker_error is not None:
                error_data = json.dumps({
                    "type": "error",
                    "error": {"type": "api_error", "message": str(worker_error)},
                })
                yield f"event: error\ndata: {error_data}\n\n"
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")

