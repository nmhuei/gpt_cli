import asyncio
import json
import logging
import shlex
import time
import uuid
from typing import Any
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

logger = logging.getLogger("gpt_cli.server")

from ..config import defaults
from ..config.settings import Settings
from ..core.token_cache import TokenCacheManager
from ..models.registry import ModelRegistry
from ..transport.wire import WireTransport
from ..pool.worker_pool import WorkerPool
from ..session.registry import SessionRegistry

def _extract_content(content_obj: Any) -> str:
    if isinstance(content_obj, str):
        return content_obj
    if isinstance(content_obj, list):
        parts: list[str] = []
        for item in content_obj:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                c_type = item.get("type")
                if c_type == "text":
                    parts.append(item.get("text", ""))
                elif c_type == "tool_result":
                    sub_c = item.get("content", "")
                    sub_text = _extract_content(sub_c)
                    prefix = "[Tool Error]" if item.get("is_error") else "[Tool Output]"
                    parts.append(f"{prefix}: {sub_text}")
                elif c_type == "tool_use":
                    t_name = item.get("name", "tool")
                    t_input = item.get("input", {})
                    parts.append(f"[Tool Call: {t_name} input={json.dumps(t_input)}]")
                elif c_type == "thinking":
                    pass
        return "\n".join([p for p in parts if p.strip()])
    return str(content_obj or "")

def _resolve_tool_name(raw_name: str, registered: set[str]) -> tuple[str, bool]:
    """Resolve upstream/wire tool label to registered harness tool name.
    Returns (resolved_name, should_unwrap_args).
    """
    if not registered or raw_name in registered:
        return raw_name, False

    reg_lower_map = {r.lower(): r for r in registered}
    if raw_name.lower() in reg_lower_map:
        return reg_lower_map[raw_name.lower()], False

    alias_map = {
        ("functions.exec", "bash", "host_run_command", "python", "terminal", "sh"): ("bash", "execute_command", "sh", "terminal", "run_command", "python"),
        ("host_list_directory", "list_directory", "dir_list", "ls", "api_tool.list_resources", "list_resources", "resources_list"): ("glob", "bash", "fs_list"),
        ("host_read_file", "read_file", "file_read", "cat"): ("read", "read_file", "fs_read"),
        ("host_write_file", "write_file", "file_write"): ("write", "write_file", "fs_write"),
        ("host_replace_in_file", "edit_file", "replace_file_content"): ("edit", "edit_file", "str_replace_editor"),
        ("host_search_text", "search_text", "grep"): ("grep", "fs_search", "search"),
    }
    raw_lower = raw_name.lower()
    for reg in registered:
        if reg.lower().endswith(f"__{raw_lower}"):
            return reg, False

    for sources, targets in alias_map.items():
        if raw_lower in sources:
            for cand in targets:
                if cand.lower() in reg_lower_map:
                    return reg_lower_map[cand.lower()], False
                for reg in registered:
                    if reg.lower().endswith(f"__{cand.lower()}"):
                        return reg, False

    if "(" in raw_name and ")" in raw_name:
        inner = raw_name.split("(", 1)[1].split(")", 1)[0].strip()
        inner_lower = inner.lower()
        if inner_lower in reg_lower_map:
            return reg_lower_map[inner_lower], True
        for reg in registered:
            if reg.lower().endswith(f"__{inner_lower}"):
                return reg, True
        for sources, targets in alias_map.items():
            if inner_lower in sources:
                for cand in targets:
                    if cand.lower() in reg_lower_map:
                        return reg_lower_map[cand.lower()], True
                    for reg in registered:
                        if reg.lower().endswith(f"__{cand.lower()}"):
                            return reg, True

    if "." in raw_name:
        tail = raw_name.rsplit(".", 1)[1].strip()
        tail_lower = tail.lower()
        if tail_lower in reg_lower_map:
            return reg_lower_map[tail_lower], True
        for reg in registered:
            if reg.lower().endswith(f"__{tail_lower}"):
                return reg, True
        for sources, targets in alias_map.items():
            if tail_lower in sources:
                for cand in targets:
                    if cand.lower() in reg_lower_map:
                        return reg_lower_map[cand.lower()], True
                    for reg in registered:
                        if reg.lower().endswith(f"__{cand.lower()}"):
                            return reg, True

    return raw_name, False

def _normalize_tool_payload(resolved_name: str, raw_payload: Any, should_unwrap: bool = False) -> dict[str, Any]:
    """Normalizes upstream tool payload into a valid JSON schema dictionary for harness tools."""
    if isinstance(raw_payload, dict):
        obj = dict(raw_payload)
    elif isinstance(raw_payload, str):
        try:
            obj = json.loads(raw_payload)
            if not isinstance(obj, dict):
                obj = {"input": obj}
        except Exception:
            obj = {"input": raw_payload}
    else:
        obj = {}

    if should_unwrap and "args" in obj and isinstance(obj["args"], dict):
        obj = dict(obj["args"])

    r_lower = resolved_name.lower()

    # Normalization for bash / command execution
    if r_lower in ("bash", "execute_command", "sh", "terminal", "run_command"):
        if "command" not in obj:
            if "paths" in obj and isinstance(obj["paths"], list):
                p_str = " ".join(shlex.quote(str(p)) for p in obj["paths"] if p)
                obj["command"] = f"ls -la {p_str}" if p_str else "ls -la"
            elif "pattern" in obj:
                obj["command"] = f"ls -la {shlex.quote(str(obj['pattern']))}"
            else:
                cmd = obj.get("input") or obj.get("code") or obj.get("script") or obj.get("cmd") or ""
                if not cmd and isinstance(raw_payload, str) and raw_payload.strip() not in ("{}", "[]", ""):
                    if not (raw_payload.strip().startswith("{") and raw_payload.strip().endswith("}")):
                        cmd = raw_payload
                if isinstance(cmd, str):
                    cmd_str = cmd.strip()
                    if ("\n" in cmd_str or "import " in cmd_str or "def " in cmd_str) and not cmd_str.startswith(("python", "bash", "sh")):
                        cmd_str = f"python3 -c {json.dumps(cmd_str)}"
                    obj["command"] = cmd_str
                else:
                    obj["command"] = str(cmd)
        if "description" not in obj:
            lines = str(obj.get("command", "")).strip().splitlines()
            first_line = lines[0][:60] if lines else ""
            obj["description"] = f"Run: {first_line}" if lines else "Execute command"
        # Prune non-standard keys so strict tools like Claude Code's Bash don't reject
        for k in ("paths", "query", "input", "code", "script", "cmd"):
            obj.pop(k, None)

    # Normalization for glob / directory listing
    elif r_lower in ("glob", "fs_list"):
        if "pattern" not in obj:
            d = obj.get("path") or obj.get("directory") or obj.get("dir") or "."
            d_str = str(d).rstrip("/")
            obj["pattern"] = f"{d_str}/*" if d_str and d_str != "." else "*"
        for k in ("directory", "dir"):
            obj.pop(k, None)

    # Normalization for file reading (dual path and file_path for Claude Code & dsh)
    elif r_lower in ("read_file", "read"):
        p = obj.get("file_path") or obj.get("path") or obj.get("file") or obj.get("input")
        if p:
            obj["path"] = str(p)
            obj["file_path"] = str(p)
        for k in ("file", "input"):
            obj.pop(k, None)

    # Normalization for file writing
    elif r_lower in ("write_file", "write"):
        p = obj.get("file_path") or obj.get("path") or obj.get("file") or obj.get("input")
        if p:
            obj["path"] = str(p)
            obj["file_path"] = str(p)
        if "content" not in obj:
            obj["content"] = obj.get("text") or obj.get("input") or ""
        for k in ("file", "input", "text"):
            obj.pop(k, None)

    # Normalization for file editing
    elif r_lower in ("edit_file", "edit", "str_replace_editor"):
        p = obj.get("file_path") or obj.get("path") or obj.get("file")
        if p:
            obj["path"] = str(p)
            obj["file_path"] = str(p)
        obj.pop("file", None)

    return obj

def format_chat_messages(
    messages: list[dict[str, Any]],
    system_prompt: str | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> str:
    rendered: list[str] = []
    sys_sections: list[str] = []
    if system_prompt and system_prompt.strip():
        sys_sections.append(system_prompt.strip())

    if tools:
        tool_descs: list[str] = []
        for t in tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            schema = t.get("input_schema") or t.get("parameters") or {}
            tool_descs.append(f"- {name}: {desc} (Parameters: {json.dumps(schema)})")
        if tool_descs:
            sys_sections.append("Available tools:\n" + "\n".join(tool_descs))

    full_system = "\n\n".join(sys_sections)
    if full_system:
        rendered.append(f"[System Instruction]\n{full_system}")

    if len(messages) == 1 and not rendered:
        return _extract_content(messages[0].get("content", ""))

    for msg in messages:
        role = msg.get("role", "user")
        content = _extract_content(msg.get("content", ""))
        if not content.strip():
            continue
        if role == "system":
            rendered.append(f"[System Instruction]\n{content.strip()}")
        elif role == "user":
            rendered.append(f"[User]\n{content.strip()}")
        elif role == "assistant":
            rendered.append(f"[Assistant]\n{content.strip()}")
        else:
            rendered.append(f"[{role.capitalize()}]\n{content.strip()}")

    return "\n\n".join(rendered)

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

async def chat_completions(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "Invalid JSON"}}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"error": {"message": "Messages cannot be empty"}}, status_code=400)

    prompt = format_chat_messages(messages)
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
                    queue.put_nowait({
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {"reasoning_content": "\n".join(new_th) + "\n"}, "finish_reason": None}]
                    })
            elif ev.event_type == "delta":
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
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield f"data: {json.dumps(item)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")

async def anthropic_messages(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Invalid JSON"}}, status_code=400)

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "Messages cannot be empty"}}, status_code=400)

    system_text = _extract_content(body.get("system", ""))
    tools = body.get("tools", [])
    registered_tools = {t.get("name") for t in tools if isinstance(t, dict) and t.get("name")}
    header_keys = {k: v for k, v in request.headers.items() if k.lower() not in ("authorization", "cookie")}
    print(f"[GATEWAY] POST /v1/messages: msgs={len(messages)}, registered_tools={list(registered_tools)}, system_len={len(system_text)}, headers={header_keys}", flush=True)

    session_key = (
        request.headers.get("x-deepseek-harness-session-id")
        or request.headers.get("x-claude-code-session-id")
        or request.headers.get("x-session-id")
        or request.headers.get("x-conversation-id")
        or (body.get("metadata") or {}).get("session_id")
    )
    sess = None
    conv_id = None
    parent_id = None
    if session_key:
        reg = SessionRegistry()
        sess = reg.get_or_create(session_id=f"sess_{str(session_key).replace('-', '_')}")
        conv_id = sess.conversation_id
        parent_id = sess.last_message_id

    if conv_id and parent_id and len(messages) > 1:
        prompt = format_chat_messages(messages[-1:], tools=tools)
    else:
        conv_id = None
        parent_id = None
        prompt = format_chat_messages(messages, system_prompt=system_text, tools=tools)

    model_alias = body.get("model", "gpt-5-6-thinking")
    is_stream = bool(body.get("stream", False))

    effort = None
    th_cfg = body.get("thinking")
    out_cfg = body.get("output_config", {})
    if isinstance(out_cfg, dict) and "effort" in out_cfg:
        e = str(out_cfg.get("effort", "")).lower()
        if e in ("high", "max"):
            effort = "extended"
        elif e in ("low", "min", "off", "none", "standard", "medium"):
            effort = "standard"
    elif isinstance(th_cfg, dict):
        if th_cfg.get("type") == "disabled":
            effort = "standard"
        elif th_cfg.get("type") == "enabled":
            effort = "extended"

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

        result = await pool.execute(do_turn)
        if sess:
            final_c = captured_conv_id or result.conversation_id
            final_m = captured_msg_id or result.message_id
            if final_c and final_m:
                reg.update_turn(sess.local_id, conversation_id=final_c, last_message_id=final_m, model=model_alias)

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
        stop_reason = "tool_use" if has_tool else "end_turn"

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

        queue.put_nowait(("message_start", {
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
                queue.put_nowait(("content_block_delta", {
                    "type": "content_block_delta",
                    "index": current_block_index,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(args_obj)
                    }
                }))
                queue.put_nowait(("content_block_stop", {
                    "type": "content_block_stop",
                    "index": current_block_index
                }))
                current_block_type = None
                active_tool_id = None
                active_tool_name = None
                active_tool_payload = None
            elif current_block_type is not None:
                queue.put_nowait(("content_block_stop", {
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
                        queue.put_nowait(("content_block_start", {
                            "type": "content_block_start",
                            "index": current_block_index,
                            "content_block": {"type": "thinking", "thinking": ""}
                        }))
                    queue.put_nowait(("content_block_delta", {
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
                    queue.put_nowait(("content_block_start", {
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
                        queue.put_nowait(("content_block_start", {
                            "type": "content_block_start",
                            "index": current_block_index,
                            "content_block": {"type": "text", "text": ""}
                        }))
                    queue.put_nowait(("content_block_delta", {
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
                    queue.put_nowait(("content_block_start", {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""}
                    }))
                    queue.put_nowait(("content_block_stop", {
                        "type": "content_block_stop",
                        "index": 0
                    }))

                stop_reason = "tool_use" if last_block_type == "tool_use" else "end_turn"
                queue.put_nowait(("message_delta", {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                    "usage": {"output_tokens": out_tokens}
                }))
                queue.put_nowait(("message_stop", {"type": "message_stop"}))

        async def worker():
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
                    if sess:
                        final_c = captured_conv_id or turn_res.conversation_id
                        final_m = captured_msg_id or turn_res.message_id
                        if final_c and final_m:
                            reg.update_turn(sess.local_id, conversation_id=final_c, last_message_id=final_m, model=model_alias)
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(pool.execute(worker))
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                event_name, data = item
                yield f"event: {event_name}\ndata: {json.dumps(data)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(event_generator(), media_type="text/event-stream")

def create_app() -> Starlette:
    settings = Settings()
    routes = [
        Route("/healthz", healthz, methods=["GET"]),
        Route("/v1/models", list_models, methods=["GET"]),
        Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        Route("/v1/messages", anthropic_messages, methods=["POST"]),
    ]
    app = Starlette(routes=routes)
    app.state.pool = WorkerPool(max_workers=settings.max_workers)
    return app
