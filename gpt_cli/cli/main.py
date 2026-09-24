import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ..config import defaults
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

    # 1. run
    run_p = subparsers.add_parser("run", help="Run a turn with the model")
    run_p.add_argument("-p", "--prompt", required=True, help="Prompt text")
    run_p.add_argument("-s", "--session", default=None, help="Local session ID to continue")
    run_p.add_argument("-n", "--new", action="store_true", help="Start a new session")
    run_p.add_argument("-t", "--tag", default=None, help="Tag for the session")
    run_p.add_argument("-m", "--model", default="gpt-5-6-thinking", help="Model alias")
    run_p.add_argument("-e", "--effort", default="extended", help="Thinking effort")
    run_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # 2. chat (interactive)
    chat_p = subparsers.add_parser("chat", help="Start interactive terminal chat session")
    chat_p.add_argument("-s", "--session", default=None, help="Continue specific session ID")
    chat_p.add_argument("-m", "--model", default="gpt-5-6-thinking", help="Model alias")

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

    return parser

async def interactive_chat(model: str = "gpt-5-6-thinking", session_id: str | None = None) -> None:
    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        print("Error: No token bundle found in cache (~/.local/share/webgpt/profiles/personal).", file=sys.stderr)
        sys.exit(1)

    reg = SessionRegistry()
    log_mgr = SessionLogManager()
    sess = reg.get_or_create(session_id=session_id)

    print(f"/home/light · {model} · personal (Wire Conduit · 0 MB RAM)")
    print(f"Session: {sess.local_id} (turns: {sess.turns})")
    print("Type /exit to quit, /new to clear session, /session for details.\n")

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
        if user_input == "/session":
            print(f"Local ID:        {sess.local_id}")
            print(f"Conversation ID: {sess.conversation_id}")
            print(f"Last Message ID: {sess.last_message_id}")
            print(f"Turns:           {sess.turns}")
            print(f"Log Dir:         {log_mgr.session_dir(sess.local_id)}\n")
            continue
        if user_input == "/help":
            print("Commands: /new, /session, /exit, /help\n")
            continue

        turn_number = sess.turns + 1
        captured_thoughts: list[str] = []
        captured_tools: list[dict[str, Any]] = []

        def on_event(ev):
            log_mgr.log_event(sess.local_id, ev, turn_index=turn_number)
            if ev.event_type == "delta":
                sys.stdout.write(ev.data.get("text", ""))
                sys.stdout.flush()
            elif ev.event_type == "thinking":
                for th in ev.data.get("thoughts", []):
                    if isinstance(th, str) and th not in captured_thoughts:
                        captured_thoughts.append(th)
            elif ev.event_type == "tool_call_started":
                captured_tools.append(ev.data)

        try:
            async with WireTransport(bundle) as transport:
                result = await transport.send_turn(
                    prompt=user_input,
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

    if getattr(args, "new", False):
        sess = reg.create_session(tag=getattr(args, "tag", None))
    elif getattr(args, "session", None):
        sess = reg.get_or_create(session_id=args.session, tag=getattr(args, "tag", None))
    else:
        sess = reg.get_or_create(tag=getattr(args, "tag", None))

    turn_number = sess.turns + 1
    captured_thoughts: list[str] = []
    captured_tools: list[dict[str, Any]] = []

    def on_event(ev):
        log_mgr.log_event(sess.local_id, ev, turn_index=turn_number)
        if ev.event_type == "thinking":
            for th in ev.data.get("thoughts", []):
                if isinstance(th, str) and th not in captured_thoughts:
                    captured_thoughts.append(th)
        elif ev.event_type == "tool_call_started":
            captured_tools.append(ev.data)
        elif not getattr(args, "json", False) and ev.event_type == "delta":
            delta_text = ev.data.get("text", "")
            sys.stdout.write(delta_text)
            sys.stdout.flush()

    err_str = None
    try:
        async with WireTransport(bundle) as transport:
            result = await transport.send_turn(
                prompt=args.prompt,
                conversation_id=sess.conversation_id,
                parent_id=sess.last_message_id,
                model_alias=getattr(args, "model", "gpt-5-6-thinking"),
                effort=getattr(args, "effort", "extended"),
                on_event=on_event
            )
    except Exception as e:
        err_str = str(e)
        log_mgr.log_turn(
            local_id=sess.local_id,
            turn_index=turn_number,
            prompt=args.prompt,
            response="",
            model=getattr(args, "model", "gpt-5-6-thinking"),
            duration_ms=0,
            conversation_id=sess.conversation_id,
            message_id=sess.last_message_id,
            error=err_str
        )
        raise

    log_mgr.log_turn(
        local_id=sess.local_id,
        turn_index=turn_number,
        prompt=args.prompt,
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

def main() -> None:
    known_commands = {"run", "chat", "session", "start", "stop", "restart", "status", "-h", "--help"}

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
            json=False
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
        asyncio.run(interactive_chat(model=args.model, session_id=args.session))
    elif args.command == "session":
        handle_session(args)
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
