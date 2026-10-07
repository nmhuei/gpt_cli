import asyncio
import json
import logging
import time
import uuid
from typing import Any
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..config.settings import Settings
from ..core.token_cache import TokenCacheManager
from ..core.types import is_completed_turn
from ..models.registry import ModelRegistry
from ..pool.worker_pool import WorkerPool
from ..session.registry import SessionRegistry
from ..telemetry.formatter import format_tool_call, format_tool_output
from ..transport.wire import WireTransport
from .gateway import (
    SSEQueueOverflow,
    _safe_queue_put,
    _validate_gateway_messages,
)
from .message_utils import _extract_content
from .prompt_formatter import format_chat_messages
from .session_title import _generate_local_session_title, _is_session_title_request
from .tool_utils import _normalize_tool_payload, _resolve_tool_name
from ..core.bridge import resolve_prompt_with_bridge


logger = logging.getLogger("gpt_cli.server")


from .sse_utils import _finish_sse_worker


from .base_routes import healthz, list_models
from .openai_routes import chat_completions
from .anthropic_routes import anthropic_messages

def create_app() -> Starlette:
    settings = Settings()
    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/health", healthz, methods=["GET"]),
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/messages", anthropic_messages, methods=["POST"]),
    ]
    app = Starlette(routes=routes)
    app.state.pool = WorkerPool(max_workers=settings.max_workers)
    return app
