import argparse
import asyncio
import contextlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from ..config import defaults as default_config
from ..config.settings import Settings
from ..core.token_cache import TokenCacheManager
from ..core.types import TurnResult, TokenBundle
from ..session.registry import SessionRegistry, SessionLookupError, SessionRegistryCorruptionError, SessionDeleteError, SessionPartialDeleteError
from ..session.logger import SessionLogManager
from ..session.models import SessionRecord
from ..session.live_turns import LiveTurnRegistry, SessionBusyError
from ..telemetry.presenter import TerminalTelemetryPresenter
from .turn_runner import SessionTurnRunner
from ..core.bridge import resolve_prompt_with_bridge as default_resolve_prompt_with_bridge

async def handle_run(args: argparse.Namespace, on_session_resolved=None, *, resolve_connector_context, defaults, token_cache, resolve_prompt_with_bridge=default_resolve_prompt_with_bridge) -> TurnResult:
    quiet = bool(getattr(args, "quiet", False))
    cache_mgr = token_cache()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        if getattr(args, "json", False) and not quiet:
            print(json.dumps({"status": "error", "message": "No valid token bundle found in disk cache"}))
        else:
            print("Error: No token bundle found in cache (~/.local/share/webgpt/profiles/personal).", file=sys.stderr)
        sys.exit(1)

    reg = SessionRegistry()
    log_mgr = SessionLogManager()
    settings = Settings()

    sess: SessionRecord | None = None
    if getattr(args, "conversation", None):
        conv_id = args.conversation
        try:
            from curl_cffi.requests import AsyncSession
            async with AsyncSession(impersonate="chrome146") as s:
                h = {"Authorization": f"Bearer {bundle.access_token}", "Accept": "application/json"}
                if bundle.chatgpt_account_id:
                    h["ChatGPT-Account-ID"] = bundle.chatgpt_account_id
                r = await s.get(f"{defaults.CHATGPT_BASE_URL}/backend-api/conversation/{conv_id}", headers=h)
                if r.status_code == 200:
                    cdata = r.json()
                    curr_node = cdata.get("current_node") or "client-created-root"
                    title = cdata.get("title") or "Web Conversation"
                    sess = reg.create_session(tag=f"web:{title[:20]}")
                    reg.checkpoint(sess.local_id, conversation_id=conv_id, message_id=curr_node, model=getattr(args, "model", "gpt-5-6-thinking"))
                    sess = reg.get_session(sess.local_id) or sess
                    if not getattr(args, "json", False) and not quiet:
                        print(f"Attached to ChatGPT Web thread: '{title}' ({conv_id})\n")
        except Exception as e:
            if not getattr(args, "json", False) and not quiet:
                print(f"Warning: Failed to attach web conversation: {e}\n", file=sys.stderr)

    if not sess:
        if getattr(args, "resume_session", None):
            sess = reg.require_session(args.resume_session)
        elif getattr(args, "resume", False):
            sess = reg.require_session(None)
        else:
            sess = reg.create_session(tag=getattr(args, "tag", None))

    if on_session_resolved is not None:
        on_session_resolved(sess.local_id)

    # Resolve prompt from either -p/--prompt, positional arguments, or stdin
    raw_prompt = getattr(args, "prompt", None)
    pos_parts = getattr(args, "pos_prompt", [])
    if not raw_prompt and pos_parts:
        raw_prompt = " ".join(pos_parts).strip()
    if not raw_prompt and not sys.stdin.isatty():
        raw_prompt = sys.stdin.read().strip()
    if not raw_prompt:
        if getattr(args, "json", False) and not quiet:
            print(json.dumps({"status": "error", "message": "Prompt is required"}))
        else:
            print("Error: Prompt is required. Example: `gpt \"Hello\"` or `gpt run \"Your task\"`", file=sys.stderr)
        sys.exit(2)

    active_bridge: str | None = None if getattr(args, "no_bridge", False) else (getattr(args, "bridge", None) or settings.default_bridge_name)
    if not active_bridge and not getattr(args, "no_bridge", False):
        m = re.match(r"^@\[?([a-zA-Z0-9_\-]+)\]?", raw_prompt)
        if m:
            active_bridge = m.group(1)

    turn_number = sess.turns + 1
    captured_thoughts: list[str] = []
    captured_tools: list[dict[str, Any]] = []
    printed_telemetry: set[str] = set()
    presenter = None if getattr(args, "json", False) or quiet else TerminalTelemetryPresenter(sys.stdout)
    checkpoint_conversation_id = sess.conversation_id
    checkpoint_message_id = sess.last_message_id
    printed_any_delta = False

    def on_event(ev):
        nonlocal checkpoint_conversation_id, checkpoint_message_id, printed_any_delta
        is_json = getattr(args, "json", False) or quiet

        if ev.event_type == "conversation_id":
            checkpoint_conversation_id = ev.data.get("conversation_id") or checkpoint_conversation_id
        elif ev.event_type == "message_id":
            checkpoint_message_id = ev.data.get("message_id") or checkpoint_message_id
        if ev.event_type in {"conversation_id", "message_id"}:
            reg.checkpoint(
                sess.local_id,
                checkpoint_conversation_id,
                checkpoint_message_id,
                getattr(args, "model", None),
            )

        if ev.event_type == "delta":
            delta_text = ev.data.get("text", "")
            if not is_json:
                printed_any_delta = True
                sys.stdout.write(delta_text)
                sys.stdout.flush()
            return
        if ev.event_type == "thinking":
            for thought in ev.data.get("thoughts", []):
                summary = thought.get("summary") if isinstance(thought, dict) else str(thought)
                if summary and summary not in captured_thoughts:
                    captured_thoughts.append(summary)
            if presenter is not None:
                presenter.emit(ev)
            return
        if ev.event_type == "reasoning_recap":
            recap = ev.data.get("recap")
            if recap and recap not in captured_thoughts:
                captured_thoughts.append(recap)
            if presenter is not None:
                presenter.emit(ev)
            return
        if ev.event_type == "tool_call_started":
            captured_tools.append(dict(ev.data))
            if presenter is not None:
                presenter.emit(ev)
            return
        if ev.event_type == "tool_call_finished":
            call_id = ev.data.get("id")
            matched = None
            if isinstance(call_id, str) and call_id:
                for candidate in reversed(captured_tools):
                    if candidate.get("id") == call_id:
                        matched = candidate
                        break
            elif len(captured_tools) == 1:
                matched = captured_tools[0]
            elif captured_tools:
                matched = captured_tools[-1]

            if matched is None:
                matched = {"id": call_id, "tool": ev.data.get("tool", "tool")}
                captured_tools.append(matched)

            matched["result"] = ev.data.get("output")
            matched["result_title"] = ev.data.get("title")
            matched["finished"] = True
            if ev.data.get("source"):
                matched["source"] = ev.data["source"]

            if presenter is not None:
                presenter.emit(ev)
            return
        if ev.event_type == "recovery_started":
            if presenter is not None:
                presenter.emit(ev)
            return
        if ev.event_type == "web_search":
            queries = ev.data.get("queries", [])
            q_str = ", ".join(queries) if queries else ""
            if q_str and q_str not in printed_telemetry:
                printed_telemetry.add(q_str)
                if not is_json:
                    sys.stdout.write(f"\033[34m🌐 [Search] {q_str}...\033[0m\n")
                    sys.stdout.flush()
        elif ev.event_type == "citations":
            cites = ev.data.get("citations", [])
            if cites and not is_json:
                c_lines = "\n".join(f"  [{i+1}] {c.get('title', 'Source')}: {c.get('url')}" for i, c in enumerate(cites))
                sys.stdout.write(f"\n\033[33m📚 Sources:\n{c_lines}\033[0m\n")
                sys.stdout.flush()

    has_explicit_bridge = bool(getattr(args, "bridge", None))
    if not has_explicit_bridge and not getattr(args, "no_bridge", False):
        m = re.match(r"^@\[?([a-zA-Z0-9_\-]+)\]?", raw_prompt)
        if m:
            has_explicit_bridge = True
            active_bridge = m.group(1)

    system_hints, selected_mcp_sources = (
        await asyncio.to_thread(resolve_connector_context, active_bridge)
        if has_explicit_bridge and active_bridge and not getattr(args, "no_bridge", False)
        else ([], [])
    )
    wire_prompt = resolve_prompt_with_bridge(
        raw_prompt,
        active_bridge,
        connector_id=system_hints[0] if system_hints else None,
    )

    last_event_time = time.monotonic()
    def on_event_with_timer(ev):
        nonlocal last_event_time
        last_event_time = time.monotonic()
        on_event(ev)

    async def heartbeat_pinger():
        while True:
            await asyncio.sleep(20)
            if time.monotonic() - last_event_time >= 20:
                if not getattr(args, "json", False) and not quiet:
                    sys.stdout.write("\033[2m⏱️ [Conduit Heartbeat] Model/tool operation in progress...\033[0m\n")
                    sys.stdout.flush()

    pinger_task = asyncio.create_task(heartbeat_pinger())
    err_str = None
    try:
        runner = SessionTurnRunner(
            registry=reg,
            live_registry=LiveTurnRegistry(defaults.user_runtime_dir()),
            log_manager=log_mgr,
            token_bundle=bundle,
            session=sess,
            model=getattr(args, "model", "gpt-5-6-thinking"),
            effort=getattr(args, "effort", "extended"),
            emit_event=on_event_with_timer,
            capture_images=bool(getattr(args, "capture_images", False)),
        )
        result = await runner.run_initial(
            wire_prompt,
            system_hints=system_hints,
            selected_mcp_sources=selected_mcp_sources,
        )
        sess = reg.require_session(sess.local_id)
    except SessionBusyError:
        raise
    except KeyboardInterrupt:
        if not getattr(args, "json", False) and not quiet:
            sys.stdout.write(f"\n\n\033[1;33m⏸️ [Interrupted by User] Session saved: {sess.local_id}\033[0m\n")
            sys.stdout.write(f"\033[2mTo resume at any time, run:\n  gpt run --resume-session {sess.local_id}\033[0m\n\n")
            sys.stdout.flush()
        sys.exit(130)
    except Exception as e:
        err_str = str(e)
        log_mgr.log_turn(
            local_id=sess.local_id,
            turn_index=turn_number,
            prompt=raw_prompt,
            response="",
            model=getattr(args, "model", "gpt-5-6-thinking"),
            duration_ms=0,
            conversation_id=sess.conversation_id,
            message_id=sess.last_message_id,
            error=err_str
        )
        raise
    finally:
        pinger_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pinger_task

    effective_msg_id = result.message_id or checkpoint_message_id or sess.last_message_id
    effective_conversation_id = result.conversation_id or checkpoint_conversation_id or sess.conversation_id or ""

    lifecycle_status = result.lifecycle_status or (
        "completed_turn" if result.status == "completed" else "transport_lost"
    )
    output_status = {
        "completed_turn": "ok",
        "model_terminal": "terminal",
    }.get(lifecycle_status, "incomplete")

    if getattr(args, "json", False) and not quiet:
        out = {
            "status": output_status,
            "lifecycle_status": lifecycle_status,
            "text": result.text,
            "session_id": sess.local_id,
            "conversation_id": effective_conversation_id,
            "message_id": effective_msg_id,
            "bqa_chat_id": sess.bqa_chat_id,
            "bqa_workspace_path": sess.bqa_workspace_path,
            "model": result.model,
            "duration_ms": result.duration_ms,
            "thinking": captured_thoughts,
            "tool_calls": captured_tools,
            "image_paths": result.image_paths,
            "image_errors": result.image_errors,
            "error": result.error,
            "log_dir": str(log_mgr.session_dir(sess.local_id))
        }
        print(json.dumps(out, ensure_ascii=False))
    elif not quiet:
        if not printed_any_delta and result.text:
            sys.stdout.write(result.text)
            sys.stdout.flush()
        for image_path in result.image_paths:
            sys.stdout.write(f"🖼️ Image saved: {image_path}\n")
        for image_error in result.image_errors:
            sys.stderr.write(f"⚠️ Image download failed: {image_error}\n")
        if lifecycle_status != "completed_turn":
            sys.stdout.write(f"\n\033[33m⚠️ [Turn Lifecycle: {lifecycle_status}]\033[0m\n")
            if result.error:
                sys.stdout.write(f"\033[2mReason: {result.error}\033[0m\n")
            sys.stdout.write("\033[2mConversation pointer has been checkpointed for inspection or an explicit later resume.\033[0m\n\n")
            sys.stdout.flush()
        else:
            sys.stdout.write("\n\n")
            sys.stdout.flush()

    return result

