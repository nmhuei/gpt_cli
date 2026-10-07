"""Agent execution runner helpers.

This module contains the agent loop implementation previously kept in cli.main.
"""

import argparse
import json
import sys

from ..session.registry import SessionRegistry
from ..session.logger import SessionLogManager


async def handle_agent(args: argparse.Namespace, *, handle_run) -> None:
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
    baseline_log_entries = 0

    # Pin the target before the first awaited turn.  handle_run creates or resolves
    # a session synchronously, but its network turn is awaited; the registry's
    # active session can change while that await is in progress.  Continuation
    # must use the session selected at agent start, not whichever session becomes
    # active later.
    explicit_session_id = getattr(args, "resume_session", None)
    if explicit_session_id:
        session = registry.require_session(explicit_session_id)
    elif getattr(args, "resume", False):
        session = registry.require_session(None)
        args.resume_session = session.local_id
        args.resume = False
    elif not getattr(args, "conversation", None):
        session = registry.create_session(tag=getattr(args, "tag", None))
        args.resume_session = session.local_id
    else:
        session = None

    if session:
        baseline_log_entries = len(SessionLogManager().get_history(session.local_id))

    target_ms = int(args.min_runtime_minutes * 60_000)
    resolved_session_id: str | None = session.local_id if session else None

    def pin_resolved_session(local_id: str) -> None:
        nonlocal resolved_session_id
        resolved_session_id = local_id

    initial_result = await handle_run(args, on_session_resolved=pin_resolved_session)
    if initial_result is None or initial_result.status not in {"completed", "terminal"} or initial_result.error:
        if getattr(args, "json", False):
            sess_id = session.local_id if session else (resolved_session_id or "")
            print(json.dumps({
                "status": "stopped",
                "session_id": sess_id,
                "runtime_duration_ms": 0,
                "target_duration_ms": target_ms,
            }, ensure_ascii=False))
        else:
            status_str = initial_result.status if initial_result else "no_result"
            err_str = f" (error: {initial_result.error})" if initial_result and initial_result.error else ""
            sys.stdout.write(
                f"\n⚠️ Agent stopping due to turn status: {status_str}{err_str}\n"
            )
            sys.stdout.flush()
        return

    # --conversation may create the session only after attaching remote metadata,
    # but the callback fires before its first awaited turn. Never consult active_id
    # after the turn has started.
    if session is None:
        if resolved_session_id is None:
            raise RuntimeError("handle_run did not resolve an agent session")
        session = SessionRegistry().require_session(resolved_session_id)
        args.resume_session = session.local_id

    session_id = session.local_id
    session = SessionRegistry().require_session(session_id)
    log_manager = SessionLogManager()

    def elapsed_ms() -> int:
        history = log_manager.get_history(session.local_id)
        return sum(int(item.get("duration_ms") or 0) for item in history[baseline_log_entries:])

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
        continuation_result = await handle_run(continuation)
        if continuation_result is None or continuation_result.status not in {"completed", "terminal"} or continuation_result.error:
            if not getattr(args, "json", False):
                status_str = continuation_result.status if continuation_result else "no_result"
                err_str = f" (error: {continuation_result.error})" if continuation_result and continuation_result.error else ""
                sys.stdout.write(
                    f"\n⚠️ Agent stopping due to turn status: {status_str}{err_str}\n"
                )
                sys.stdout.flush()
            break
        session = SessionRegistry().require_session(session.local_id)
        elapsed = elapsed_ms()

    if args.json:
        print(json.dumps({
            "status": "runtime_target_reached" if elapsed >= target_ms else "stopped",
            "session_id": session.local_id,
            "runtime_duration_ms": elapsed,
            "target_duration_ms": target_ms,
        }, ensure_ascii=False))
    elif elapsed >= target_ms:
        sys.stdout.write(
            f"\033[32m✓ Agent runtime target reached: {elapsed / 60_000:.1f}/"
            f"{args.min_runtime_minutes:.1f} min in {session.local_id}\033[0m\n"
        )
        sys.stdout.flush()
    else:
        sys.stdout.write(
            f"\033[33m⏸️ Agent stopped: {elapsed / 60_000:.1f}/"
            f"{args.min_runtime_minutes:.1f} min in {session.local_id}\033[0m\n"
        )
        sys.stdout.flush()
