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

async def chat_completions(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)

    body, error = _validate_gateway_messages(body)
    if error:
        return error
    messages = body["messages"]

    settings = Settings()
    model_alias = body.get("model", settings.default_model)
    effort = body.get("effort", settings.default_effort)
    is_stream = bool(body.get("stream", False))

    if _is_session_title_request(messages):
        title = _generate_local_session_title(messages)
        if is_stream:
            async def title_stream():
                completion_id = f"chatcmpl-title-{uuid.uuid4().hex[:8]}"
                now = int(time.time())
                chunks = (
                    {"id": completion_id, "object": "chat.completion.chunk", "created": now, "model": model_alias,
                     "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                    {"id": completion_id, "object": "chat.completion.chunk", "created": now, "model": model_alias,
                     "choices": [{"index": 0, "delta": {"content": title}, "finish_reason": None}]},
                    {"id": completion_id, "object": "chat.completion.chunk", "created": now, "model": model_alias,
                     "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                )
                for chunk in chunks:
                    yield f"data: {json.dumps(chunk)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(title_stream(), media_type="text/event-stream")
        return JSONResponse({
            "id": f"chatcmpl-title-{uuid.uuid4().hex[:8]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model_alias,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": title}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        })

    settings = Settings()
    tools_list = body.get("tools", [])
    active_bridge = None if tools_list else settings.default_bridge_name
    prompt = format_chat_messages(messages, tools=tools_list)
    prompt = resolve_prompt_with_bridge(prompt, active_bridge)

    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        return JSONResponse({"error": {"message": "No valid token bundle on disk"}}, status_code=500)

    pool: WorkerPool = request.app.state.pool

    if not is_stream:
        async def do_turn():
            async with WireTransport(bundle) as transport:
                return await transport.send_turn(
                    prompt=prompt,
                    model_alias=model_alias,
                    effort=effort,
                )

        try:
            result = await pool.execute(do_turn)
        except Exception as exc:
            logger.exception("Error executing chat completion")
            return JSONResponse(
                {"error": {"message": str(exc), "type": "api_error"}},
                status_code=500,
            )
        if not is_completed_turn(result):
            return JSONResponse(
                {"error": {"message": result.error or "Turn did not complete", "type": "api_error"}},
                status_code=500,
            )
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        now = int(time.time())
        return JSONResponse({
            "id": completion_id,
            "object": "chat.completion",
            "created": now,
            "model": result.model,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": result.text
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": len(prompt.split()),
                "completion_tokens": len(result.text.split()),
                "total_tokens": len(prompt.split()) + len(result.text.split())
            }
        })

    # Streaming mode
    async def event_generator():
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=100)
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        def on_event(ev):
            if ev.event_type == "thinking":
                new_th = []
                for th in ev.data.get("thoughts", []):
                    s = th.get("summary") if isinstance(th, dict) else str(th)
                    if s:
                        new_th.append(s)
                title = ev.data.get("title")
                if title:
                    new_th.append(title)
                if new_th:
                    _safe_queue_put(queue, {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"reasoning_content": "\n".join(new_th) + "\n"}, "finish_reason": None}]
                    })
            elif ev.event_type == "reasoning_recap":
                recap = ev.data.get("recap", "")
                if recap:
                    _safe_queue_put(queue, {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"reasoning_content": f"[{recap}]\n"}, "finish_reason": None}]
                    })
            elif ev.event_type == "delta":
                text = ev.data.get("text", "")
                if text:
                    _safe_queue_put(queue, {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]
                    })
            elif ev.event_type == "tool_call_started":
                tool_label = ev.data.get("tool") or ev.data.get("name") or "tool"
                payload = ev.data.get("payload") or ""
                title, input_details = format_tool_call(tool_label, payload)
                tool_block = f"\n{title}\n"
                if input_details:
                    tool_block += f"📥 {input_details}\n"
                _safe_queue_put(queue, {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"reasoning_content": tool_block}, "finish_reason": None}]
                })
            elif ev.event_type == "tool_call_finished":
                tool_label = ev.data.get("tool") or ev.data.get("name") or "tool"
                output = ev.data.get("output") or ""
                title = ev.data.get("title") or ""
                formatted_out = format_tool_output(tool_label, output, title)
                tool_block = f"📤 Output:\n{formatted_out}\n\n"
                _safe_queue_put(queue, {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"reasoning_content": tool_block}, "finish_reason": None}]
                })
            elif ev.event_type == "done":
                _safe_queue_put(queue, {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                })

        overflowed = False
        worker_error: Exception | None = None

        async def worker():
            nonlocal overflowed, worker_error
            try:
                async with WireTransport(bundle) as transport:
                    result = await transport.send_turn(
                        prompt=prompt,
                        model_alias=model_alias,
                        effort=effort,
                        on_event=on_event
                    )
                    if not is_completed_turn(result):
                        worker_error = RuntimeError(result.error or "Turn did not complete")
            except SSEQueueOverflow:
                overflowed = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                worker_error = exc
                logger.exception("Error in chat completion stream worker")
            finally:
                await _finish_sse_worker(queue)

        task = asyncio.create_task(pool.execute(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
            if overflowed:
                yield "data: {\"error\": {\"message\": \"SSE queue overflow\"}}\n\n"
                return
            if worker_error is not None:
                err_payload = json.dumps({"error": {"message": str(worker_error)}})
                yield f"data: {err_payload}\n\n"
                return
            yield "data: [DONE]\n\n"
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")

