import asyncio
import json
import time
import uuid
from typing import Any
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..config import defaults
from ..core.token_cache import TokenCacheManager
from ..models.registry import ModelRegistry
from ..transport.wire import WireTransport
from ..pool.worker_pool import WorkerPool
from ..session.registry import SessionRegistry

def _extract_content(content_obj: Any) -> str:
    if isinstance(content_obj, str):
        return content_obj
    if isinstance(content_obj, list):
        parts = []
        for item in content_obj:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content_obj or "")

async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "model": "gpt-5-6-thinking",
        "transport": "wire_conduit",
        "max_workers": defaults.DEFAULT_MAX_WORKERS
    })

async def list_models(request: Request) -> JSONResponse:
    models = [
        {"id": m.slug, "object": "model", "owned_by": "openai"}
        for m in ModelRegistry.all_models()
    ]
    return JSONResponse({"object": "list", "data": models})

async def chat_completions(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": {"message": "Messages cannot be empty"}}, status_code=400)

    prompt = _extract_content(messages[-1].get("content", ""))
    model_alias = body.get("model", "gpt-5-6-thinking")
    is_stream = bool(body.get("stream", False))

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
                )

        result = await pool.execute(do_turn)
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
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        def on_event(ev):
            if ev.event_type == "delta":
                text = ev.data.get("text", "")
                if text:
                    queue.put_nowait({
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]
                    })
            elif ev.event_type == "done":
                queue.put_nowait({
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                })

        async def worker():
            try:
                async with WireTransport(bundle) as transport:
                    await transport.send_turn(
                        prompt=prompt,
                        model_alias=model_alias,
                        on_event=on_event
                    )
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(pool.execute(worker))
        while True:
            item = await queue.get()
            if item is None:
                break
            yield f"data: {json.dumps(item)}\n\n"
        yield "data: [DONE]\n\n"
        await task

    return StreamingResponse(event_generator(), media_type="text/event-stream")

async def anthropic_messages(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Messages cannot be empty"}}, status_code=400)

    prompt = _extract_content(messages[-1].get("content", ""))
    model_alias = body.get("model", "gpt-5-6-thinking")
    is_stream = bool(body.get("stream", False))

    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": "No valid token bundle"}}, status_code=500)

    pool: WorkerPool = request.app.state.pool
    msg_id = f"msg_{uuid.uuid4().hex[:20]}"

    if not is_stream:
        async def do_turn():
            async with WireTransport(bundle) as transport:
                return await transport.send_turn(
                    prompt=prompt,
                    model_alias=model_alias,
                )

        result = await pool.execute(do_turn)
        return JSONResponse({
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "model": result.model,
            "content": [{"type": "text", "text": result.text}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {
                "input_tokens": len(prompt.split()),
                "output_tokens": len(result.text.split())
            }
        })

    # Streaming Anthropic events
    async def event_generator():
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        queue.put_nowait(("message_start", {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "model": "gpt-5-6-thinking",
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": len(prompt.split()), "output_tokens": 0}
            }
        }))
        queue.put_nowait(("content_block_start", {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""}
        }))

        out_tokens = 0
        def on_event(ev):
            nonlocal out_tokens
            if ev.event_type == "delta":
                text = ev.data.get("text", "")
                if text:
                    out_tokens += len(text.split())
                    queue.put_nowait(("content_block_delta", {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": text}
                    }))
            elif ev.event_type == "done":
                queue.put_nowait(("content_block_stop", {"type": "content_block_stop", "index": 0}))
                queue.put_nowait(("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": out_tokens}
                }))
                queue.put_nowait(("message_stop", {"type": "message_stop"}))

        async def worker():
            try:
                async with WireTransport(bundle) as transport:
                    await transport.send_turn(
                        prompt=prompt,
                        model_alias=model_alias,
                        on_event=on_event
                    )
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(pool.execute(worker))
        while True:
            item = await queue.get()
            if item is None:
                break
            event_name, data = item
            yield f"event: {event_name}\ndata: {json.dumps(data)}\n\n"
        await task

    return StreamingResponse(event_generator(), media_type="text/event-stream")

def create_app() -> Starlette:
    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/messages", anthropic_messages, methods=["POST"]),
    ]
    app = Starlette(routes=routes)
    app.state.pool = WorkerPool(max_workers=defaults.DEFAULT_MAX_WORKERS)
    return app
