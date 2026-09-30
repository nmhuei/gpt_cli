import argparse
import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from ..config import defaults
from ..config.settings import Settings
from ..core.token_cache import TokenCacheManager
from ..session.registry import SessionRegistry
from ..session.logger import SessionLogManager
from ..transport.wire import WireTransport
from ..server.daemon import start_daemon, stop_daemon, status_daemon

def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpt",
        description="WebGPT Remake - High-performance Wire Runtime & Gateway"
    )
    subparsers = parser.add_subparsers(dest="command", required=False)

    settings = Settings()

    # 1. run
    run_p = subparsers.add_parser("run", help="Run a turn with the model")
    run_p.add_argument("-p", "--prompt", required=True, help="Prompt text")
    run_p.add_argument("-s", "--session", default=None, help="Local session ID to continue")
    run_p.add_argument("-n", "--new", action="store_true", help="Start a new session")
    run_p.add_argument("-t", "--tag", default=None, help="Tag for the session")
    run_p.add_argument("-m", "--model", default=settings.default_model, help="Model alias")
    run_p.add_argument("-e", "--effort", default=settings.default_effort, help="Thinking effort")
    run_p.add_argument("-b", "--bridge", default=None, help=f"Active MCP bridge/connector name (default: {settings.default_bridge_name})")
    run_p.add_argument("--no-bridge", action="store_true", help="Disable auto-bridge routing")
    run_p.add_argument("--conv", default=None, help="Continue an existing ChatGPT Web conversation UUID")
    run_p.add_argument("--max-turns", type=int, default=1, help="Max autonomous turns to execute (default: 1)")
    run_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # 2. chat (interactive)
    chat_p = subparsers.add_parser("chat", help="Start interactive terminal chat session")
    chat_p.add_argument("-s", "--session", default=None, help="Continue specific session ID")
    chat_p.add_argument("-m", "--model", default=settings.default_model, help="Model alias")
    chat_p.add_argument("-b", "--bridge", default=None, help=f"Active MCP bridge/connector name (default: {settings.default_bridge_name})")
    chat_p.add_argument("--no-bridge", action="store_true", help="Disable auto-bridge routing")
    chat_p.add_argument("--conv", default=None, help="Attach directly to an existing ChatGPT Web conversation UUID")

    # 3. session
    sess_p = subparsers.add_parser("session", help="Manage tool-created sessions")
    sess_sub = sess_p.add_subparsers(dest="session_action", required=True)
    
    # session list
    list_p = sess_sub.add_parser("list", help="List tool sessions")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # session show
    show_p = sess_sub.add_parser("show", help="Show session details")
    show_p.add_argument("session_id", help="Local session ID")
    show_p.add_argument("--json", action="store_true", help="Output JSON")

    # session log
    log_p = sess_sub.add_parser("log", help="View session transcript, prompts, responses, and debug logs")
    log_p.add_argument("session_id", nargs="?", default=None, help="Local session ID (default: active session)")
    log_p.add_argument("--turn", type=int, default=None, help="Filter to specific turn number")
    log_p.add_argument("--tail", type=int, default=None, help="Show last N turns")
    log_p.add_argument("--events", action="store_true", help="Show granular telemetry events instead of turn history")
    log_p.add_argument("--json", action="store_true", help="Output JSON format")

    # session set
    set_p = sess_sub.add_parser("set", help="Set active session")
    set_p.add_argument("session_id", help="Local session ID")

    # session delete
    del_p = sess_sub.add_parser("delete", help="Delete a session")
    del_p.add_argument("session_id", help="Local session ID")

    # 4. Gateway control
    start_p = subparsers.add_parser("start", help="Start gateway daemon")
    start_p.add_argument("--host", default=defaults.DEFAULT_GATEWAY_HOST)
    start_p.add_argument("--port", type=int, default=defaults.DEFAULT_GATEWAY_PORT)

    stop_p = subparsers.add_parser("stop", help="Stop gateway daemon")
    restart_p = subparsers.add_parser("restart", help="Restart gateway daemon")
    restart_p.add_argument("--host", default=defaults.DEFAULT_GATEWAY_HOST)
    restart_p.add_argument("--port", type=int, default=defaults.DEFAULT_GATEWAY_PORT)

    status_p = subparsers.add_parser("status", help="Check gateway daemon status")
    status_p.add_argument("--json", action="store_true", help="Output JSON")

    # 5. plugin / mcp management
    for cmd_name in ("plugin", "mcp"):
        plug_p = subparsers.add_parser(cmd_name, help=f"Manage ChatGPT Web plugins & MCP connectors ({cmd_name})")
        plug_sub = plug_p.add_subparsers(dest="plugin_action", required=True)

        # list
        p_list = plug_sub.add_parser("list", help="List custom MCP plugins/connectors")
        p_list.add_argument("--all", action="store_true", help="Include built-in system connectors")
        p_list.add_argument("--json", action="store_true", help="Output JSON")

        # add
        p_add = plug_sub.add_parser("add", help="Add a new MCP plugin with auto full_access permissions")
        p_add.add_argument("name", nargs="?", default=None, help=f"Plugin / Connector name (default: {settings.default_bridge_name})")
        p_add.add_argument("url", nargs="?", default=None, help="Public MCP server endpoint URL (default: auto from tunnel_url.txt)")
        p_add.add_argument("--desc", default="", help="Description")
        p_add.add_argument("--permission", default=settings.default_mcp_permission, choices=["full_access", "low_risk", "read_only", "always_ask"], help="Permission level (default: full_access = Allow all tools / ELEVATED RISK)")
        p_add.add_argument("--json", action="store_true", help="Output JSON")

        # delete / rm
        p_del = plug_sub.add_parser("delete", help="Delete an MCP plugin by name or ID")
        p_del.add_argument("identifier", help="Plugin name, Link ID, or Connector ID")
        p_del.add_argument("--json", action="store_true", help="Output JSON")

        p_rm = plug_sub.add_parser("rm", help="Delete an MCP plugin by name or ID (alias for delete)")
        p_rm.add_argument("identifier", help="Plugin name, Link ID, or Connector ID")
        p_rm.add_argument("--json", action="store_true", help="Output JSON")

        # permission
        p_perm = plug_sub.add_parser("permission", help="Configure permission level for a plugin")
        p_perm.add_argument("identifier", help="Plugin name, Link ID, or Connector ID")
        p_perm.add_argument("level", nargs="?", default="full_access", choices=["full_access", "low_risk", "read_only", "always_ask"], help="Permission level (default: full_access)")
        p_perm.add_argument("--json", action="store_true", help="Output JSON")

        # sync
        p_sync = plug_sub.add_parser("sync", help="Synchronize MCP plugin URL with latest tunnel URL")
        p_sync.add_argument("name", nargs="?", default=None, help="Plugin name to sync (default: configured default bridge)")
        p_sync.add_argument("--json", action="store_true", help="Output JSON")

    # 6. web (ChatGPT Web conversations)
    web_p = subparsers.add_parser("web", help="Interact with ChatGPT Web conversations")
    web_sub = web_p.add_subparsers(dest="web_action", required=True)
    web_list_p = web_sub.add_parser("list", help="List recent conversations from ChatGPT Web")
    web_list_p.add_argument("--limit", type=int, default=15, help="Number of conversations to display (default: 15)")
    web_list_p.add_argument("--json", action="store_true", help="Output JSON")

    return parser

def resolve_prompt_with_bridge(prompt: str, bridge_name: str | None = None) -> str:
    """Prepend active bridge mention (e.g. '@brige ') if not already present."""
    p = prompt.strip()
    if not bridge_name or str(bridge_name).lower() in ("off", "none", "false"):
        return p
    clean_bridge = str(bridge_name).lstrip("@").strip()
    if not clean_bridge:
        return p
    if p.startswith(f"@{clean_bridge} ") or p == f"@{clean_bridge}":
        return p
    return f"@{clean_bridge} {p}"

async def interactive_chat(
    model: str = "gpt-5-6-thinking",
    session_id: str | None = None,
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
    sess = reg.get_or_create(session_id=session_id)
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

        def on_event(ev):
            log_mgr.log_event(sess.local_id, ev, turn_index=turn_number)
            if ev.event_type == "delta":
                sys.stdout.write(ev.data.get("text", ""))
                sys.stdout.flush()
            elif ev.event_type == "thinking":
                title = ev.data.get("title")
                if title and title not in printed_telemetry:
                    printed_telemetry.add(title)
                    sys.stdout.write(f"\033[2m💭 [Thinking] {title}\033[0m\n")
                    sys.stdout.flush()
                for th in ev.data.get("thoughts", []):
                    summ = th.get("summary") if isinstance(th, dict) else str(th)
                    if summ and summ not in printed_telemetry:
                        printed_telemetry.add(summ)
                        captured_thoughts.append(summ)
                        sys.stdout.write(f"\033[2m💭 [Thinking] {summ}\033[0m\n")
                        sys.stdout.flush()
            elif ev.event_type == "reasoning_recap":
                recap_str = ev.data.get("recap")
                if recap_str and recap_str not in printed_telemetry:
                    printed_telemetry.add(recap_str)
                    sys.stdout.write(f"\033[2m⏱️ [{recap_str}]\033[0m\n")
                    sys.stdout.flush()
            elif ev.event_type == "tool_call_started":
                captured_tools.append(ev.data)
                call_id = ev.data.get("id")
                if not call_id or call_id not in printed_telemetry:
                    if call_id:
                        printed_telemetry.add(call_id)
                    tool_name = ev.data.get("tool", "tool")
                    payload = ev.data.get("payload")
                    payload_str = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload or "")
                    sys.stdout.write(f"\n\033[36m🔧 [Tool Call: {tool_name}]\033[0m\n")
                    if payload_str:
                        sys.stdout.write(f"\033[2m📥 Input: {payload_str}\033[0m\n")
                    sys.stdout.flush()
            elif ev.event_type == "tool_call_finished":
                call_id = ev.data.get("id")
                fin_key = f"fin_{call_id}" if call_id else None
                if not fin_key or fin_key not in printed_telemetry:
                    if fin_key:
                        printed_telemetry.add(fin_key)
                    title = ev.data.get("title") or ""
                    output = ev.data.get("output") or ""
                    out_str = str(output)[:2000] if output else ""
                    if len(str(output)) > 2000:
                        out_str += f"\n... [truncated {len(str(output)) - 2000} bytes]"
                    sys.stdout.write(f"\033[36m📤 [Tool Output{f': {title}' if title else ''}]\033[0m\n")
                    if out_str:
                        sys.stdout.write(f"\033[2m{out_str}\033[0m\n")
                    sys.stdout.flush()
            elif ev.event_type == "web_search":
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

        wire_prompt = resolve_prompt_with_bridge(user_input, active_bridge)
        try:
            async with WireTransport(bundle) as transport:
                result = await transport.send_turn(
                    prompt=wire_prompt,
                    conversation_id=sess.conversation_id,
                    parent_id=sess.last_message_id,
                    model_alias=model,
                    on_event=on_event
                )
            sys.stdout.write("\n\n")
            sys.stdout.flush()

            log_mgr.log_turn(
                local_id=sess.local_id,
                turn_index=turn_number,
                prompt=user_input,
                response=result.text,
                model=result.model,
                duration_ms=result.duration_ms,
                conversation_id=result.conversation_id,
                message_id=result.message_id,
                thinking=captured_thoughts,
                tool_calls=captured_tools
            )

            reg.update_turn(
                local_id=sess.local_id,
                conversation_id=result.conversation_id,
                last_message_id=result.message_id,
                model=result.model
            )
            sess = reg.get_session(sess.local_id) or sess
        except Exception as e:
            print(f"\nError: {e}\n", file=sys.stderr)

async def handle_run(args: argparse.Namespace) -> None:
    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "message": "No valid token bundle found in disk cache"}))
        else:
            print("Error: No token bundle found in cache (~/.local/share/webgpt/profiles/personal).", file=sys.stderr)
        sys.exit(1)

    reg = SessionRegistry()
    log_mgr = SessionLogManager()
    settings = Settings()

    if getattr(args, "conv", None):
        conv_id = args.conv
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
                    if not getattr(args, "json", False):
                        print(f"Attached to ChatGPT Web thread: '{title}' ({conv_id})\n")
        except Exception as e:
            if not getattr(args, "json", False):
                print(f"Warning: Failed to attach web conversation: {e}\n", file=sys.stderr)
    elif getattr(args, "new", False):
        sess = reg.create_session(tag=getattr(args, "tag", None))
    elif getattr(args, "session", None):
        sess = reg.get_or_create(session_id=args.session, tag=getattr(args, "tag", None))
    else:
        sess = reg.get_or_create(tag=getattr(args, "tag", None))

    active_bridge: str | None = None if getattr(args, "no_bridge", False) else (getattr(args, "bridge", None) or settings.default_bridge_name)
    max_turns = max(1, getattr(args, "max_turns", 1))
    current_prompt = args.prompt
    last_result = None

    async with WireTransport(bundle) as transport:
        for turn_idx in range(max_turns):
            turn_number = sess.turns + 1
            captured_thoughts: list[str] = []
            captured_tools: list[dict[str, Any]] = []
            printed_telemetry: set[str] = set()

            def on_event(ev):
                log_mgr.log_event(sess.local_id, ev, turn_index=turn_number)
                if getattr(args, "json", False):
                    return
                if ev.event_type == "delta":
                    delta_text = ev.data.get("text", "")
                    sys.stdout.write(delta_text)
                    sys.stdout.flush()
                elif ev.event_type == "thinking":
                    title = ev.data.get("title")
                    if title and title not in printed_telemetry:
                        printed_telemetry.add(title)
                        sys.stdout.write(f"\033[2m💭 [Thinking] {title}\033[0m\n")
                        sys.stdout.flush()
                    for th in ev.data.get("thoughts", []):
                        summ = th.get("summary") if isinstance(th, dict) else str(th)
                        if summ and summ not in printed_telemetry:
                            printed_telemetry.add(summ)
                            captured_thoughts.append(summ)
                            sys.stdout.write(f"\033[2m💭 [Thinking] {summ}\033[0m\n")
                            sys.stdout.flush()
                elif ev.event_type == "reasoning_recap":
                    recap_str = ev.data.get("recap")
                    if recap_str and recap_str not in printed_telemetry:
                        printed_telemetry.add(recap_str)
                        sys.stdout.write(f"\033[2m⏱️ [{recap_str}]\033[0m\n")
                        sys.stdout.flush()
                elif ev.event_type == "tool_call_started":
                    captured_tools.append(ev.data)
                    call_id = ev.data.get("id")
                    if not call_id or call_id not in printed_telemetry:
                        if call_id:
                            printed_telemetry.add(call_id)
                        tool_name = ev.data.get("tool", "tool")
                        payload = ev.data.get("payload")
                        payload_str = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload or "")
                        sys.stdout.write(f"\n\033[36m🔧 [Tool Call: {tool_name}]\033[0m\n")
                        if payload_str:
                            sys.stdout.write(f"\033[2m📥 Input: {payload_str}\033[0m\n")
                        sys.stdout.flush()
                elif ev.event_type == "tool_call_finished":
                    call_id = ev.data.get("id")
                    fin_key = f"fin_{call_id}" if call_id else None
                    if not fin_key or fin_key not in printed_telemetry:
                        if fin_key:
                            printed_telemetry.add(fin_key)
                        title = ev.data.get("title") or ""
                        output = ev.data.get("output") or ""
                        out_str = str(output)[:2000] if output else ""
                        if len(str(output)) > 2000:
                            out_str += f"\n... [truncated {len(str(output)) - 2000} bytes]"
                        sys.stdout.write(f"\033[36m📤 [Tool Output{f': {title}' if title else ''}]\033[0m\n")
                        if out_str:
                            sys.stdout.write(f"\033[2m{out_str}\033[0m\n")
                        sys.stdout.flush()
                elif ev.event_type == "web_search":
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

            wire_prompt = resolve_prompt_with_bridge(current_prompt, active_bridge)

            last_event_time = time.monotonic()
            def on_event_with_timer(ev):
                nonlocal last_event_time
                last_event_time = time.monotonic()
                on_event(ev)

            async def heartbeat_pinger():
                while True:
                    await asyncio.sleep(20)
                    if time.monotonic() - last_event_time >= 20:
                        if not getattr(args, "json", False):
                            sys.stdout.write("\033[2m⏱️ [Conduit Heartbeat] Model/tool operation in progress...\033[0m\n")
                            sys.stdout.flush()

            pinger_task = asyncio.create_task(heartbeat_pinger())
            err_str = None
            try:
                result = await transport.send_turn(
                    prompt=wire_prompt,
                    conversation_id=sess.conversation_id,
                    parent_id=sess.last_message_id,
                    model_alias=getattr(args, "model", "gpt-5-6-thinking"),
                    effort=getattr(args, "effort", "extended"),
                    on_event=on_event_with_timer
                )
            except Exception as e:
                err_str = str(e)
                log_mgr.log_turn(
                    local_id=sess.local_id,
                    turn_index=turn_number,
                    prompt=current_prompt,
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

            log_mgr.log_turn(
                local_id=sess.local_id,
                turn_index=turn_number,
                prompt=current_prompt,
                response=result.text,
                model=result.model,
                duration_ms=result.duration_ms,
                conversation_id=result.conversation_id,
                message_id=result.message_id,
                thinking=captured_thoughts,
                tool_calls=captured_tools
            )

            updated_sess = reg.update_turn(
                local_id=sess.local_id,
                conversation_id=result.conversation_id,
                last_message_id=result.message_id,
                model=result.model
            )
            sess = updated_sess or reg.get_session(sess.local_id) or sess
            last_result = result

            flag_file = Path("flag.txt")
            if flag_file.is_file() and flag_file.read_text(encoding="utf-8").strip():
                break

            import re
            flag_match = re.search(r"(?:ASIS|FLAG|CTF|flag|seccon)\{[A-Za-z0-9_!@#$%^&*()+\-=\[\]]{4,}\}", result.text)
            if flag_match:
                break

            prog_match = re.search(r"@@CTF_PROGRESS@@\s*(\{.*?\})", result.text)
            if prog_match:
                try:
                    pdata = json.loads(prog_match.group(1))
                    cand = str(pdata.get("candidate_flag", "")).strip()
                    if cand and cand not in ("...", "<FLAG>", "FLAG") and "..." not in cand and pdata.get("phase") == "solved":
                        break
                except Exception:
                    pass

            solver_file = Path("solver/solve.py")
            if solver_file.is_file():
                try:
                    content = solver_file.read_text(encoding="utf-8", errors="replace")
                    if "# TODO" not in content and len(content) > 150 and "solve(" in content:
                        break
                except Exception:
                    pass

            if turn_idx + 1 < max_turns:
                current_prompt = "Proceed with the next step. Implement the required decoder/exploit logic, execute it, save the recovered flag to ./flag.txt, and write the working solver script to ./solver/solve.py."
    result = last_result

    if getattr(args, "json", False):
        out = {
            "status": "ok",
            "text": result.text,
            "session_id": sess.local_id,
            "conversation_id": result.conversation_id,
            "message_id": result.message_id,
            "model": result.model,
            "duration_ms": result.duration_ms,
            "log_dir": str(log_mgr.session_dir(sess.local_id))
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write("\n")
        sys.stdout.flush()

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

    elif args.session_action == "delete":
        ok = reg.delete_session(args.session_id)
        if ok:
            print(f"Deleted session: {args.session_id}")
        else:
            print(f"Session {args.session_id} not found.", file=sys.stderr)
            sys.exit(1)

def handle_plugin(args: argparse.Namespace) -> None:
    from ..plugins.manager import PluginManager, VALID_PERMISSIONS

    try:
        mgr = PluginManager()
    except Exception as e:
        print(f"Error initializing PluginManager: {e}", file=sys.stderr)
        sys.exit(1)

    action = getattr(args, "plugin_action", None)

    if action == "list":
        plugins = mgr.list_plugins(include_system=getattr(args, "all", False))
        if getattr(args, "json", False):
            print(json.dumps([p.raw for p in plugins], indent=2))
            return

        if not plugins:
            print("No custom MCP plugins found. Use 'gpt plugin add <name> <url>' to register one.")
            return

        print(f"{'NAME':<20} {'PERMISSION':<32} {'TOOLS':<7} {'LINK ID':<38}")
        print("-" * 100)
        for p in plugins:
            perm_str = p.permission_display
            tools_count = str(len(p.actions))
            print(f"{p.name:<20} {perm_str:<32} {tools_count:<7} {p.link_id:<38}")

    elif action == "add":
        print(
            "[TẠM ĐÓNG BĂNG] Tính năng tự động add plugin qua CLI tạm thời được đóng băng theo yêu cầu.\n"
            "Vui lòng thêm và kết nối MCP plugin thủ công trên ChatGPT Web:\n"
            "  1. Vào Settings -> Connected apps (hoặc Developer mode)\n"
            "  2. Bấm 'Create MCP Connector' / 'Connect' và dán URL tunnel\n"
            "  3. Cấp quyền 'Allow all tools (ELEVATED RISK)'",
            file=sys.stderr
        )
        sys.exit(0)

    elif action in ("delete", "rm"):
        target = args.identifier
        if not getattr(args, "json", False):
            print(f"Deleting plugin '{target}'...")
        try:
            res = mgr.delete_plugin(target)
            if getattr(args, "json", False):
                out = {
                    "identifier": res.identifier,
                    "link_deleted": res.link_deleted,
                    "connector_deleted": res.connector_deleted,
                    "success": res.success,
                    "error": res.error,
                }
                print(json.dumps(out, indent=2))
                if not res.success:
                    sys.exit(1)
            else:
                if res.success:
                    print(f"✓ Plugin '{target}' deleted successfully.")
                else:
                    err_info = f": {res.error}" if res.error else ""
                    print(f"Plugin '{target}' not found or could not be completely deleted{err_info}.", file=sys.stderr)
                    sys.exit(1)
        except Exception as e:
            if getattr(args, "json", False):
                print(json.dumps({"success": False, "error": str(e)}, indent=2))
            else:
                print(f"Error deleting plugin: {e}", file=sys.stderr)
            sys.exit(1)

    elif action in ("permission", "perm", "set-permission"):
        target = args.identifier
        level = getattr(args, "level", "full_access") or "full_access"
        if not getattr(args, "json", False):
            print(f"Updating permission for '{target}' to {level}...")
        try:
            info = mgr.set_permission(target, permission=level)
            if getattr(args, "json", False):
                print(json.dumps(info.raw, indent=2))
            else:
                print(f"✓ Permission for '{info.name}' set to {info.permission_display} ({info.apps_privacy_control}).")
        except Exception as e:
            if getattr(args, "json", False):
                print(json.dumps({"error": str(e)}, indent=2))
            else:
                print(f"Error updating permission: {e}", file=sys.stderr)
            sys.exit(1)

    elif action == "sync":
        print(
            "[TẠM ĐÓNG BĂNG] Tính năng tự động sync plugin tạm thời được đóng băng theo yêu cầu.\n"
            "Vui lòng cập nhật URL tunnel thủ công trên ChatGPT Web nếu tunnel thay đổi.",
            file=sys.stderr
        )
        sys.exit(0)

def handle_web(args: argparse.Namespace) -> None:
    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        print("Error: No valid token bundle found in cache.", file=sys.stderr)
        sys.exit(1)

    from curl_cffi import requests
    headers = {
        "Authorization": f"Bearer {bundle.access_token}",
        "User-Agent": defaults.DEFAULT_USER_AGENT,
        "Accept": "application/json",
    }
    if bundle.chatgpt_account_id:
        headers["ChatGPT-Account-ID"] = bundle.chatgpt_account_id

    action = getattr(args, "web_action", None)
    if action == "list":
        limit = getattr(args, "limit", 15)
        url = f"{defaults.CHATGPT_BASE_URL}/backend-api/conversations?offset=0&limit={limit}&order=updated"
        try:
            s = requests.Session(impersonate="chrome146")
            if bundle.cookies:
                s.cookies.update(bundle.cookies)
            resp = s.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                print(f"Error fetching web conversations: HTTP {resp.status_code}", file=sys.stderr)
                sys.exit(1)
            items = resp.json().get("items", [])
            if getattr(args, "json", False):
                print(json.dumps(items, indent=2))
            else:
                print(f"\n{'ID':<38} {'UPDATED':<20} TITLE")
                print("-" * 80)
                for it in items:
                    cid = it.get("id", "")
                    up = (it.get("update_time", "") or "")[:19].replace("T", " ")
                    title = it.get("title", "(no title)")
                    print(f"{cid:<38} {up:<20} {title}")
                print(f"\nTotal: {len(items)} conversations. Resume any thread with: gpt chat --conv <ID>\n")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

def main() -> None:
    known_commands = {"run", "chat", "session", "web", "start", "stop", "restart", "status", "plugin", "mcp", "-h", "--help"}

    # Case 1: Plain `gpt` with no arguments -> interactive chat!
    if len(sys.argv) == 1:
        asyncio.run(interactive_chat())
        return

    # Case 2: `gpt "some prompt text"` without `run -p`
    first_arg = sys.argv[1]
    if first_arg not in known_commands and not first_arg.startswith("-"):
        prompt = " ".join(sys.argv[1:]).strip()
        args = argparse.Namespace(
            command="run",
            prompt=prompt,
            session=None,
            new=False,
            tag=None,
            model="gpt-5-6-thinking",
            effort="extended",
            json=False,
            bridge=None,
            no_bridge=False,
            conv=None
        )
        asyncio.run(handle_run(args))
        return

    parser = build_cli_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        asyncio.run(handle_run(args))
    elif args.command == "chat":
        asyncio.run(interactive_chat(
            model=args.model,
            session_id=args.session,
            bridge=args.bridge,
            no_bridge=args.no_bridge,
            conv_id=getattr(args, "conv", None)
        ))
    elif args.command == "session":
        handle_session(args)
    elif args.command == "web":
        handle_web(args)
    elif args.command in ("plugin", "mcp"):
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
        else:
            if st["running"]:
                print(f"Gateway is running (PID {st['pid']}).")
            else:
                print("Gateway is stopped.")

if __name__ == "__main__":
    main()
