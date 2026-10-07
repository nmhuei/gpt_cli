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
from .tool_utils import _normalize_tool_payload, _resolve_tool_name

logger = logging.getLogger("gpt_cli.server")

async def healthz(request: Request) -> JSONResponse:
    settings = Settings()
    pool = getattr(request.app.state, "pool", None)
    active_workers = pool.active_workers if pool else 0
    return JSONResponse({
        "status": "ok",
        "model": settings.default_model,
        "transport": "wire_conduit",
        "max_workers": settings.max_workers,
        "active_workers": active_workers,
    })

async def list_models(request: Request) -> JSONResponse:
    models = [
        {"id": m.slug, "object": "model", "owned_by": "openai"}
        for m in ModelRegistry.all_models()
    ]
    return JSONResponse({"object": "list", "data": models})

