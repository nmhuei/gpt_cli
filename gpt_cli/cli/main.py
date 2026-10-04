import argparse
import asyncio
import contextlib
import json
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import defaults
from ..config.settings import Settings
from ..core.token_cache import TokenCacheManager
from ..session.registry import SessionRegistry
from ..session.logger import SessionLogManager
from ..session.live_turns import LiveTurnRegistry
from ..session.turn_control import request_steer
from ..transport.wire import WireTransport
from ..server.daemon import start_daemon, stop_daemon, status_daemon
from ..telemetry.presenter import TerminalTelemetryPresenter
from .turn_runner import SessionTurnRunner
from .parser import build_cli_parser, parse_cli_args
from ..plugins.manager import PluginManager, PluginInfo
from .plugin import handle_plugin as _handle_plugin

from ..core.bridge import resolve_connector_context, resolve_prompt_with_bridge


async def interactive_chat(
    model: str = "gpt-5-6-thinking",
    session_id: str | None = None,
    resume: bool = False,
    bridge: str | None = None,
    no_bridge: bool = False,
    conv_id: str | None = None,
) -> None:
    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        print("Error: No token bundle found in cache (~/.local/share/webgpt/profiles/personal).", file=sys.stderr)
        sys.exit(1)

    reg = SessionRegistry()
    log_mgr = SessionLogManager()
    sess = reg.require_session(session_id) if (resume or session_id) else reg.create_session()
    settings = Settings()

    if conv_id:
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
                    reg.update_turn(sess.local_id, conversation_id=conv_id, last_message_id=curr_node, model=model)
                    sess = reg.get_session(sess.local_id) or sess
                    print(f"Attached to ChatGPT Web thread: '{title}' ({conv_id})\n")
                else:
                    print(f"Warning: Failed to fetch conversation {conv_id}: HTTP {r.status_code}\n", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Failed to attach web conversation: {e}\n", file=sys.stderr)

    active_bridge: str | None = None if no_bridge else (bridge or settings.default_bridge_name)
    bridge_disp = f" · Bridge: @{active_bridge}" if active_bridge else ""

    print(f"/home/light · {model} · personal{bridge_disp} (Wire Conduit · 0 MB RAM)")
    print(f"Session: {sess.local_id} (turns: {sess.turns})")
    print("Type /exit to quit, /new to clear session, /bridge [name|off] for bridge, /session for details.\n")

    while True:
        try:
            user_input = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue
        if user_input in {"/exit", "/quit"}:
            print("Exiting.")
            break
        if user_input == "/new":
            sess = reg.create_session()
            print(f"Started fresh session: {sess.local_id}\n")
            continue
        if user_input.startswith("/bridge"):
            parts = user_input.split(maxsplit=1)
            if len(parts) > 1:
                arg = parts[1].strip()
                if arg.lower() in ("off", "none", "disable"):
                    active_bridge = None
                    print("Bridge routing disabled.\n")
                else:
                    active_bridge = arg.lstrip("@")
                    print(f"Active bridge set to: @{active_bridge}\n")
            else:
                curr = f"@{active_bridge}" if active_bridge else "none"
                print(f"Current active bridge: {curr}\n")
            continue
        if user_input == "/session":
            print(f"Local ID:        {sess.local_id}")
            print(f"Conversation ID: {sess.conversation_id}")
            print(f"Last Message ID: {sess.last_message_id}")
            print(f"Turns:           {sess.turns}")
            print(f"Active Bridge:   {active_bridge or 'none'}")
            print(f"Log Dir:         {log_mgr.session_dir(sess.local_id)}\n")
            continue
        if user_input == "/help":
            print("Commands: /new, /session, /bridge [name|off], /exit, /help\n")
            continue

        turn_number = sess.turns + 1
        captured_thoughts: list[str] = []
        captured_tools: list[dict[str, Any]] = []
        printed_telemetry: set[str] = set()
        presenter = TerminalTelemetryPresenter(sys.stdout)

        def on_event(ev):
            if ev.event_type == "delta":
                sys.stdout.write(ev.data.get("text", ""))
                sys.stdout.flush()
                return
            if ev.event_type == "thinking":
                for thought in ev.data.get("thoughts", []):
                    summary = thought.get("summary") if isinstance(thought, dict) else str(thought)
                    if summary:
                        captured_thoughts.append(summary)
                presenter.emit(ev)
                return
            if ev.event_type == "tool_call_started":
                captured_tools.append(dict(ev.data))
                presenter.emit(ev)
                return
            if ev.event_type in {"tool_call_finished", "reasoning_recap", "recovery_started"}:
                presenter.emit(ev)
                return
            if ev.event_type == "web_search":
                queries = ev.data.get("queries", [])
                q_str = ", ".join(queries) if queries else ""
                if q_str and q_str not in printed_telemetry:
                    printed_telemetry.add(q_str)
                    sys.stdout.write(f"\033[34m🌐 [Search] {q_str}...\033[0m\n")
                    sys.stdout.flush()
            elif ev.event_type == "citations":
                cites = ev.data.get("citations", [])
                if cites:
                    c_lines = "\n".join(f"  [{i+1}] {c.get('title', 'Source')}: {c.get('url')}" for i, c in enumerate(cites))
                    sys.stdout.write(f"\n\033[33m📚 Sources:\n{c_lines}\033[0m\n")
                    sys.stdout.flush()

        system_hints, selected_mcp_sources = (
            await asyncio.to_thread(resolve_connector_context, active_bridge)
            if active_bridge
            else ([], [])
        )
        wire_prompt = resolve_prompt_with_bridge(
            user_input,
            active_bridge,
            connector_id=system_hints[0] if system_hints else None,
        )
        try:
            runner = SessionTurnRunner(
                registry=reg,
                live_registry=LiveTurnRegistry(defaults.user_runtime_dir()),
                log_manager=log_mgr,
                token_bundle=bundle,
                session=sess,
                model=model,
                effort="extended",
                emit_event=on_event,
            )
            result = await runner.run_initial(
                wire_prompt,
                system_hints=system_hints,
                selected_mcp_sources=selected_mcp_sources,
            )
            for image_path in result.image_paths:
                print(f"🖼️ Image saved: {image_path}")
            for image_error in result.image_errors:
                print(f"⚠️ Image download failed: {image_error}", file=sys.stderr)
            sys.stdout.write("\n\n")
            sys.stdout.flush()
            sess = reg.get_session(sess.local_id) or sess
        except Exception as e:
            print(f"\nError: {e}\n", file=sys.stderr)

async def handle_run(args: argparse.Namespace):
    quiet = bool(getattr(args, "quiet", False))
    cache_mgr = TokenCacheManager()
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
                    reg.update_turn(sess.local_id, conversation_id=conv_id, last_message_id=curr_node, model=getattr(args, "model", "gpt-5-6-thinking"))
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
        if ev.event_type in {"tool_call_finished", "recovery_started"}:
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


async def handle_image(args: argparse.Namespace) -> None:
    """Run one fresh image-focused turn and require a downloaded image artifact."""
    prompt = f"Create an image using the image-generation tool for this description:\n\n{args.prompt}"
    run_args = argparse.Namespace(**vars(args))
    run_args.command = "run"
    run_args.prompt = prompt
    run_args.pos_prompt = []
    run_args.resume = False
    run_args.resume_session = None
    run_args.conversation = None
    run_args.tag = "image"
    run_args.bridge = None
    run_args.no_bridge = True
    run_args.quiet = True
    run_args.capture_images = True
    result = await handle_run(run_args)

    if not result.image_paths:
        message = "No image artifact was returned by the image-generation tool."
        if result.image_errors:
            message += " " + "; ".join(result.image_errors)
        if args.json:
            print(json.dumps({
                "status": "error",
                "message": message,
                "image_paths": [],
                "image_errors": result.image_errors,
                "conversation_id": result.conversation_id,
            }, ensure_ascii=False))
        else:
            print(message, file=sys.stderr)
        raise SystemExit(1)

    if args.json:
        print(json.dumps({
            "status": "ok",
            "image_paths": result.image_paths,
            "image_errors": result.image_errors,
            "conversation_id": result.conversation_id,
            "message_id": result.message_id,
        }, ensure_ascii=False))
    else:
        for image_path in result.image_paths:
            print(f"🖼️ Image saved: {image_path}")
        for image_error in result.image_errors:
            print(f"⚠️ Image download failed: {image_error}", file=sys.stderr)


async def handle_agent(args: argparse.Namespace) -> None:
    """Keep a single server-side DAG active until its measured runtime budget is met.

    ChatGPT decides whether one individual turn is terminal.  The CLI cannot
    inspect hidden reasoning, so an agent run deliberately measures the
    provider-visible upstream duration and creates ordinary follow-up turns
    from the latest durable parent node when a turn ends early.
    """
    if args.min_runtime_minutes <= 0:
        raise ValueError("--min-runtime-minutes must be greater than zero")
    if args.json:
        # The agent owns JSON stdout; individual internal turns stay in logs.
        args.quiet = True

    registry = SessionRegistry()
    baseline_turns = 0
    if getattr(args, "resume_session", None):
        baseline_turns = registry.require_session(args.resume_session).turns
    elif getattr(args, "resume", False):
        baseline_turns = registry.require_session(None).turns

    target_ms = int(args.min_runtime_minutes * 60_000)
    original_prompt = args.prompt
    original_parts = list(args.pos_prompt)
    await handle_run(args)

    session = SessionRegistry().require_session(None)
    log_manager = SessionLogManager()

    def elapsed_ms() -> int:
        history = log_manager.get_history(session.local_id)
        return sum(int(item.get("duration_ms") or 0) for item in history[baseline_turns:])

    elapsed = elapsed_ms()
    while elapsed < target_ms:
        if not args.json:
            sys.stdout.write(
                f"\033[2m🤖 Agent continuing in {session.local_id} "
                f"({elapsed / 60_000:.1f}/{args.min_runtime_minutes:.1f} min)…\033[0m\n"
            )
            sys.stdout.flush()

        continuation = argparse.Namespace(**vars(args))
        continuation.command = "run"
        continuation.prompt = "continue"
        continuation.pos_prompt = []
        continuation.resume = False
        continuation.resume_session = session.local_id
        continuation.conversation = None
        await handle_run(continuation)
        session = SessionRegistry().require_session(session.local_id)
        elapsed = elapsed_ms()

    if args.json:
        print(json.dumps({
            "status": "runtime_target_reached",
            "session_id": session.local_id,
            "runtime_duration_ms": elapsed,
            "target_duration_ms": target_ms,
        }, ensure_ascii=False))
    else:
        sys.stdout.write(
            f"\033[32m✓ Agent runtime target reached: {elapsed / 60_000:.1f}/"
            f"{args.min_runtime_minutes:.1f} min in {session.local_id}\033[0m\n"
        )
        sys.stdout.flush()


async def handle_steer(args: argparse.Namespace) -> None:
    raw_prompt = getattr(args, "prompt", None) or " ".join(getattr(args, "pos_prompt", [])).strip()
    if not raw_prompt:
        print("Error: Steering instruction is required.", file=sys.stderr)
        raise SystemExit(2)

    registry = SessionRegistry()
    live_registry = LiveTurnRegistry(defaults.user_runtime_dir())
    if getattr(args, "resume_session", None):
        session_id = registry.require_session(args.resume_session).local_id
    elif getattr(args, "resume", False):
        session_id = registry.require_session(None).local_id
    else:
        live_sessions = live_registry.list_live()
        if len(live_sessions) != 1:
            ids = ", ".join(record.session_id for record in live_sessions) or "none"
            print(f"Error: expected exactly one running session; found: {ids}", file=sys.stderr)
            raise SystemExit(2)
        session_id = live_sessions[0].session_id

    live = live_registry.get_live(session_id)
    if live is None:
        print(f"Error: local session is not running: {session_id}", file=sys.stderr)
        raise SystemExit(2)
    try:
        response = await request_steer(
            Path(live.owner.socket_path), live.owner.owner_id, uuid.uuid4().hex, raw_prompt
        )
    except OSError as exc:
        print(f"Error: live turn control is unavailable for {session_id}: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if getattr(args, "json", False):
        print(json.dumps(response, ensure_ascii=False))
    else:
        print(f"{response.get('status', 'rejected')}: {session_id}")
    if response.get("status") == "rejected":
        raise SystemExit(2)


def handle_session(args: argparse.Namespace) -> None:
    reg = SessionRegistry()
    log_mgr = SessionLogManager()
    active = reg.get_active()

    if args.session_action == "list":
        sessions = reg.list_sessions()
        if args.json:
            out = {
                "active_id": active.local_id if active else None,
                "sessions": [
                    {**s.to_dict(), "log_dir": str(log_mgr.session_dir(s.local_id))}
                    for s in sessions
                ]
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
        else:
            if not sessions:
                print("No sessions found.")
                return
            print(f"{'ACTIVE':<8} {'LOCAL ID':<16} {'TURNS':<6} {'TAG':<15} {'UPDATED AT'}")
            print("-" * 65)
            for s in sessions:
                is_active = "*" if active and active.local_id == s.local_id else " "
                tag_str = s.tag or "-"
                print(f"{is_active:<8} {s.local_id:<16} {s.turns:<6} {tag_str:<15} {s.updated_at[:19]}")

    elif args.session_action == "show":
        sess = reg.get_session(args.session_id)
        if not sess:
            print(f"Session {args.session_id} not found.", file=sys.stderr)
            sys.exit(1)
        data = sess.to_dict()
        data["log_dir"] = str(log_mgr.session_dir(sess.local_id))
        if args.json:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            print(f"Local ID:        {sess.local_id}")
            print(f"Conversation ID: {sess.conversation_id}")
            print(f"Last Message ID: {sess.last_message_id}")
            print(f"Turns:           {sess.turns}")
            print(f"Tag:             {sess.tag}")
            print(f"Updated At:      {sess.updated_at}")
            print(f"Log Directory:   {log_mgr.session_dir(sess.local_id)}")

    elif args.session_action == "log":
        target_id = args.session_id or (active.local_id if active else None)
        if not target_id:
            print("Error: No session specified and no active session found.", file=sys.stderr)
            sys.exit(1)

        if args.events:
            events = log_mgr.get_events(target_id, turn_index=args.turn)
            if args.json:
                print(json.dumps({"session_id": target_id, "events": events}, ensure_ascii=False, indent=2))
            else:
                print(f"=== EVENTS FOR SESSION: {target_id} ===")
                for ev in events:
                    t_idx = ev.get("turn_index")
                    ts = ev.get("timestamp", "")[:19]
                    etype = ev.get("event_type")
                    print(f"[{ts}] [Turn #{t_idx}] {etype}: {ev.get('data')}")
        else:
            history = log_mgr.get_history(target_id)
            if args.turn is not None:
                history = [h for h in history if h.get("turn_index") == args.turn]
            if args.tail is not None and args.tail > 0:
                history = history[-args.tail:]

            if args.json:
                print(json.dumps({"session_id": target_id, "history": history}, ensure_ascii=False, indent=2))
            else:
                formatted = log_mgr.format_readable_log(target_id, max_turns=args.tail)
                print(formatted)

    elif args.session_action == "set":
        sess = reg.get_session(args.session_id)
        if not sess:
            print(f"Session {args.session_id} not found.", file=sys.stderr)
            sys.exit(1)
        reg.set_active(args.session_id)
        print(f"Active session set to: {args.session_id}")

    elif args.session_action == "new":
        sess = reg.create_session(tag=getattr(args, "tag", None))
        if getattr(args, "json", False):
            print(json.dumps({
                "status": "created",
                "session_id": sess.local_id,
                "tag": sess.tag,
                "turns": sess.turns,
            }, ensure_ascii=False))
        else:
            print(f"Created fresh session: {sess.local_id} (active)")

    elif args.session_action == "delete":
        ok = reg.delete_session(args.session_id)
        if ok:
            print(f"Deleted session: {args.session_id}")
        else:
            print(f"Session {args.session_id} not found.", file=sys.stderr)
            sys.exit(1)



def handle_plugin(args: argparse.Namespace) -> None:
    return _handle_plugin(args, plugin_manager_cls=PluginManager)



def main() -> None:
    known_commands = {"run", "agent", "image", "chat", "steer", "session", "plugin", "start", "stop", "restart", "status", "-h", "--help"}
    if len(sys.argv) == 1:
        asyncio.run(interactive_chat())
        return

    first_arg = sys.argv[1]
    if first_arg not in known_commands and not first_arg.startswith("-"):
        prompt = " ".join(sys.argv[1:]).strip()
        args = argparse.Namespace(
            command="run", prompt=prompt, pos_prompt=[], resume=False,
            resume_session=None, conversation=None, tag=None,
            model="gpt-5-6-thinking", effort="extended", json=False,
            bridge=None, no_bridge=False,
        )
        asyncio.run(handle_run(args))
        return

    parser = build_cli_parser()
    args = parse_cli_args(sys.argv[1:])
    if not args.command:
        parser.print_help()
        sys.exit(0)
    if args.command == "run":
        asyncio.run(handle_run(args))
    elif args.command == "agent":
        asyncio.run(handle_agent(args))
    elif args.command == "image":
        asyncio.run(handle_image(args))
    elif args.command == "chat":
        asyncio.run(interactive_chat(
            model=args.model,
            session_id=args.resume_session,
            resume=args.resume,
            bridge=args.bridge,
            no_bridge=args.no_bridge,
            conv_id=args.conversation,
        ))
    elif args.command == "steer":
        asyncio.run(handle_steer(args))
    elif args.command == "session":
        handle_session(args)
    elif args.command == "plugin":
        handle_plugin(args)
    elif args.command == "start":
        start_daemon(host=args.host, port=args.port)
    elif args.command == "stop":
        stop_daemon()
    elif args.command == "restart":
        stop_daemon()
        start_daemon(host=args.host, port=args.port)
    elif args.command == "status":
        st = status_daemon()
        if args.json:
            print(json.dumps(st))
        elif st["running"]:
            print(f"Gateway is running (PID {st['pid']}).")
        else:
            print("Gateway is stopped.")

if __name__ == "__main__":
    main()
