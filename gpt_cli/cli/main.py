import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ..config import defaults
from ..core.token_cache import TokenCacheManager
from ..session.registry import SessionRegistry
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

    # 2. session
    sess_p = subparsers.add_parser("session", help="Manage tool-created sessions")
    sess_sub = sess_p.add_subparsers(dest="session_action", required=True)
    
    # session list
    list_p = sess_sub.add_parser("list", help="List tool sessions")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # session show
    show_p = sess_sub.add_parser("show", help="Show session details")
    show_p.add_argument("session_id", help="Local session ID")
    show_p.add_argument("--json", action="store_true", help="Output JSON")

    # session set
    set_p = sess_sub.add_parser("set", help="Set active session")
    set_p.add_argument("session_id", help="Local session ID")

    # session delete
    del_p = sess_sub.add_parser("delete", help="Delete a session")
    del_p.add_argument("session_id", help="Local session ID")

    # 3. Gateway control
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

async def handle_run(args: argparse.Namespace) -> None:
    cache_mgr = TokenCacheManager()
    bundle = cache_mgr.load_disk_cache()
    if not bundle:
        if args.json:
            print(json.dumps({"status": "error", "message": "No valid token bundle found in disk cache"}))
        else:
            print("Error: No token bundle found in cache (~/.local/share/webgpt/profiles/personal).", file=sys.stderr)
        sys.exit(1)

    reg = SessionRegistry()
    if args.new:
        sess = reg.create_session(tag=args.tag)
    elif args.session:
        sess = reg.get_or_create(session_id=args.session, tag=args.tag)
    else:
        sess = reg.get_or_create(tag=args.tag)

    full_output = []
    def on_event(ev):
        if not args.json and ev.event_type == "delta":
            delta_text = ev.data.get("text", "")
            sys.stdout.write(delta_text)
            sys.stdout.flush()

    async with WireTransport(bundle) as transport:
        result = await transport.send_turn(
            prompt=args.prompt,
            conversation_id=sess.conversation_id,
            parent_id=sess.last_message_id,
            model_alias=args.model,
            effort=args.effort,
            on_event=on_event
        )

    # Update session in registry with DAG parent/leaf IDs
    reg.update_turn(
        local_id=sess.local_id,
        conversation_id=result.conversation_id,
        last_message_id=result.message_id,
        model=result.model
    )

    if args.json:
        out = {
            "status": "ok",
            "text": result.text,
            "session_id": sess.local_id,
            "conversation_id": result.conversation_id,
            "message_id": result.message_id,
            "model": result.model,
            "duration_ms": result.duration_ms
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write("\n")
        sys.stdout.flush()

def handle_session(args: argparse.Namespace) -> None:
    reg = SessionRegistry()
    active = reg.get_active()

    if args.session_action == "list":
        sessions = reg.list_sessions()
        if args.json:
            out = {
                "active_id": active.local_id if active else None,
                "sessions": [s.to_dict() for s in sessions]
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
        if args.json:
            print(json.dumps(sess.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Local ID:        {sess.local_id}")
            print(f"Conversation ID: {sess.conversation_id}")
            print(f"Last Message ID: {sess.last_message_id}")
            print(f"Turns:           {sess.turns}")
            print(f"Tag:             {sess.tag}")
            print(f"Updated At:      {sess.updated_at}")

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
    parser = build_cli_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "run":
        asyncio.run(handle_run(args))
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
