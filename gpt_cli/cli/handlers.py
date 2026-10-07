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
from ..session.registry import SessionRegistry, SessionLookupError, SessionRegistryCorruptionError, SessionDeleteError, SessionPartialDeleteError
from ..session.logger import SessionLogManager
from ..session.live_turns import LiveTurnRegistry, SessionBusyError, LiveTurnRegistryCorruptionError
from ..session.models import SessionRecord
from ..session.turn_control import request_steer
from ..telemetry.presenter import TerminalTelemetryPresenter
from ..core.bridge import resolve_connector_context, resolve_prompt_with_bridge
from .turn_runner import SessionTurnRunner


async def handle_image(args: argparse.Namespace, handle_run=None) -> None:
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
    if handle_run is None:
        handle_run = globals()["handle_run"]
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

async def handle_agent(args: argparse.Namespace, handle_run=None) -> None:
    """Compatibility wrapper for the extracted agent runner."""
    from .agent_runner import handle_agent as _handle_agent

    if handle_run is None:
        handle_run = globals()["handle_run"]
    await _handle_agent(args, handle_run=handle_run)


async def handle_research(args: argparse.Namespace, handle_run=None) -> None:
    """Run structured deep technical research."""
    from .research_runner import handle_research as _handle_research

    if handle_run is None:
        handle_run = globals()["handle_run"]
    await _handle_research(args, handle_run=handle_run)

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
        sess = reg.require_session(args.session_id)
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
            raise SessionLookupError("No session specified and no active session found")

        if args.events:
            events = log_mgr.get_events(target_id, turn_index=args.turn)
            if args.tail is not None and args.tail > 0:
                events = events[-args.tail:]
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
                formatted = log_mgr.format_readable_log(target_id, max_turns=args.tail, turn_index=args.turn)
                print(formatted)

    elif args.session_action == "set":
        sess = reg.require_session(args.session_id)
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
        # Validate the filesystem target before touching registry state.
        log_mgr.session_dir(args.session_id)
        reg.require_session(args.session_id)
        live_registry = LiveTurnRegistry(defaults.user_runtime_dir())
        def delete_local_session():
            try:
                log_mgr.delete_session_logs(args.session_id)
            except Exception as exc:
                raise SessionDeleteError(f"Failed to remove local logs for session {args.session_id}: {exc}") from exc
            try:
                return reg.delete_session(args.session_id)
            except Exception as exc:
                raise SessionPartialDeleteError(
                    f"Logs removed but failed to update registry for session {args.session_id}: {exc}"
                ) from exc
        deleted = live_registry.delete_if_idle(args.session_id, delete_local_session)
        if not deleted:
            raise SessionLookupError(f"Session already deleted: {args.session_id}")
        print(json.dumps({"status": "deleted", "session_id": args.session_id}) if getattr(args, "json", False) else f"Deleted session: {args.session_id}")


async def handle_login(args: argparse.Namespace) -> None:
    """Launch interactive browser login to refresh or switch ChatGPT account."""
    from ..transport.lifebuoy import BrowserLifebuoy
    from ..server.daemon import status_daemon, stop_daemon, start_daemon

    cache_mgr = TokenCacheManager()
    lifebuoy = BrowserLifebuoy(cache_mgr)
    timeout = getattr(args, "timeout", 300)
    await lifebuoy.interactive_login(timeout=timeout)

    st = status_daemon()
    if st.get("running"):
        stop_daemon()
        start_daemon()
        print("🔄 Restarted gateway daemon with updated credentials.")

