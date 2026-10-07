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
from ..core.types import TurnResult
from ..session.registry import SessionRegistry, SessionLookupError, SessionRegistryCorruptionError, SessionDeleteError, SessionPartialDeleteError
from ..session.logger import SessionLogManager
from ..session.live_turns import LiveTurnRegistry, SessionBusyError, LiveTurnRegistryCorruptionError
from ..session.models import SessionRecord
from ..session.turn_control import request_steer
from ..transport.wire import WireTransport
from ..server.daemon import start_daemon, stop_daemon, status_daemon
from ..telemetry.presenter import TerminalTelemetryPresenter
from .turn_runner import SessionTurnRunner
from .parser import build_cli_parser, parse_cli_args
from ..plugins.manager import PluginManager, PluginInfo
from .plugin import handle_plugin as _handle_plugin
from . import handlers as _handlers
from .run_command import handle_run as _run_command

from ..core.bridge import resolve_connector_context, resolve_prompt_with_bridge


async def handle_run(*args, **kwargs):
    return await _run_command(
        *args,
        resolve_connector_context=resolve_connector_context,
        defaults=defaults,
        token_cache=TokenCacheManager,
        **kwargs,
    )


async def handle_image(args, **kwargs):
    return await _handlers.handle_image(args, handle_run=handle_run, **kwargs)


async def handle_research(args, **kwargs):
    return await _handlers.handle_research(args, handle_run=handle_run, **kwargs)


async def handle_steer(*args, **kwargs):
    return await _handlers.handle_steer(*args, **kwargs)


def handle_session(*args, **kwargs):
    return _handlers.handle_session(*args, **kwargs)


async def handle_login(args, **kwargs):
    return await _handlers.handle_login(args, **kwargs)



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
    try:
        sess = reg.require_session(session_id) if (resume or session_id) else reg.create_session()
    except SessionLookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
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
                    reg.checkpoint(sess.local_id, conversation_id=conv_id, message_id=curr_node, model=model)
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
        printed_any_delta = False

        def on_event(ev):
            nonlocal printed_any_delta
            if ev.event_type == "delta":
                printed_any_delta = True
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
            if not printed_any_delta and result.text:
                sys.stdout.write(result.text)
                sys.stdout.flush()
            for image_path in result.image_paths:
                print(f"🖼️ Image saved: {image_path}")
            for image_error in result.image_errors:
                print(f"⚠️ Image download failed: {image_error}", file=sys.stderr)
            sys.stdout.write("\n\n")
            sys.stdout.flush()
            sess = reg.get_session(sess.local_id) or sess
        except Exception as e:
            print(f"\nError: {e}\n", file=sys.stderr)












def handle_plugin(args: argparse.Namespace) -> None:
    return _handle_plugin(args, plugin_manager_cls=PluginManager)



def _main() -> None:
    # Reserve the removed command name so it is not treated as a free-form prompt.
    known_commands = {"run", "agent", "research", "image", "chat", "steer", "session", "plugin", "start", "stop", "restart", "status", "login", "help", "-h", "--help"}
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
        try:
            asyncio.run(handle_run(args))
        except (SessionLookupError, SessionBusyError, SessionRegistryCorruptionError, LiveTurnRegistryCorruptionError, SessionDeleteError, SessionPartialDeleteError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    parser = build_cli_parser()
    args = parse_cli_args(sys.argv[1:])
    if not args.command:
        parser.print_help()
        sys.exit(0)
    try:
        if args.command == "run":
            asyncio.run(handle_run(args))
        elif args.command == "research":
            asyncio.run(handle_research(args))
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
        elif args.command == "login":
            asyncio.run(handle_login(args))

    except (SessionLookupError, SessionBusyError, SessionRegistryCorruptionError, LiveTurnRegistryCorruptionError, SessionDeleteError, SessionPartialDeleteError, ValueError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "message": str(exc)}))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    """Run the CLI with one boundary for user-facing failures."""
    try:
        _main()
    except SystemExit:
        raise
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Cancelled by user.", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        if "--json" in sys.argv[1:]:
            print(json.dumps({"status": "error", "message": message}))
        else:
            print(f"Error: {message}", file=sys.stderr)
        raise SystemExit(1) from None

if __name__ == "__main__":
    main()
