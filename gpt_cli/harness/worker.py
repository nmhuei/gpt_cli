#!/usr/bin/env python3
"""GPT-CLI Wire Conduit to Muteki NDJSON Worker Bridge.

Converts ChatGPT Web wire streaming telemetry into standard Muteki line-oriented
events (StreamStep) over standard stdout, preserving full CoT and untruncated tool output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from ..config.settings import Settings
from ..core.token_cache import TokenCacheManager
from ..core.types import TokenBundle
from ..session.registry import SessionRegistry
from ..telemetry.formatter import format_tool_call, format_tool_output
from ..transport.wire import WireTransport


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)


from ..core.bridge import resolve_prompt_with_bridge



def _parser() -> argparse.ArgumentParser:
    settings = Settings()
    parser = argparse.ArgumentParser(description="Muteki GPT-CLI Wire Conduit Worker")
    parser.add_argument("--version", action="store_true", help="Print worker version")
    parser.add_argument("--session", default="", help="Session ID")
    parser.add_argument("-m", "--model", default=settings.default_model, help="Model alias")
    parser.add_argument("-e", "--effort", default=settings.default_effort, help="Thinking effort")
    parser.add_argument("-b", "--bridge", default=settings.default_bridge_name, help="Bridge connector name")
    parser.add_argument("--no-bridge", action="store_true", help="Disable bridge prefix")
    parser.add_argument("--session-root", default="", help="Custom session root directory")
    parser.add_argument("prompt", nargs=argparse.REMAINDER, help="User prompt")
    return parser


async def run_worker() -> int:
    args = _parser().parse_args()
    if args.version:
        print("muteki-gpt-wire-worker 0.1.0")
        return 0

    prompt_parts = list(args.prompt)
    if prompt_parts and prompt_parts[0] == "--":
        prompt_parts = prompt_parts[1:]
    prompt = " ".join(prompt_parts).strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        print("GPT Wire Worker requires a prompt", file=sys.stderr)
        return 2

    session_id = str(args.session or f"session-{uuid.uuid4().hex[:12]}")
    _emit({"type": "session", "id": session_id})

    reg = SessionRegistry(storage_dir=args.session_root) if args.session_root else SessionRegistry()
    sess = reg.get_or_create(session_id=session_id, tag="harness-worker")

    # Load tokens
    token_mgr = TokenCacheManager()
    bundle = token_mgr.load_disk_cache()
    if not bundle or not bundle.access_token:
        _emit({
            "type": "error",
            "session_id": session_id,
            "error": "Authentication error: no valid ChatGPT Web tokens found. Run `gpt` first to login.",
        })
        return 1

    active_bridge = None if args.no_bridge else (args.bridge or Settings().default_bridge_name)
    wire_prompt = resolve_prompt_with_bridge(prompt, active_bridge)

    captured_thoughts: list[str] = []
    captured_tools: list[dict[str, Any]] = []
    seen_call_ids: set[str] = set()
    completed_call_ids: set[str] = set()
    checkpoint_conversation_id = sess.conversation_id
    checkpoint_message_id = sess.last_message_id

    def on_event(ev: Any) -> None:
        nonlocal checkpoint_conversation_id, checkpoint_message_id
        etype = getattr(ev, "event_type", None)
        edata = getattr(ev, "data", {}) or {}

        if etype == "conversation_id":
            checkpoint_conversation_id = edata.get("conversation_id") or checkpoint_conversation_id
        elif etype == "message_id":
            checkpoint_message_id = edata.get("message_id") or checkpoint_message_id
        if etype in {"conversation_id", "message_id"}:
            reg.checkpoint(
                sess.local_id,
                checkpoint_conversation_id,
                checkpoint_message_id,
                args.model,
            )

        if etype == "thinking":
            title = edata.get("title")
            if title and title not in captured_thoughts:
                captured_thoughts.append(title)
                _emit({"type": "reasoning", "session_id": session_id, "text": title, "thinking": True})
            for th in edata.get("thoughts", []):
                summ = th.get("summary") if isinstance(th, dict) else str(th)
                if summ and summ not in captured_thoughts:
                    captured_thoughts.append(summ)
                    _emit({"type": "reasoning", "session_id": session_id, "text": summ, "thinking": True})

        elif etype == "reasoning_recap":
            recap = edata.get("recap")
            if recap:
                _emit({"type": "reasoning", "session_id": session_id, "text": f"[{recap}]", "thinking": True})

        elif etype == "tool_call_started":
            call_id = edata.get("id") or f"call_{len(seen_call_ids)+1}"
            if call_id not in seen_call_ids:
                seen_call_ids.add(call_id)
                captured_tools.append(edata)
                tool_name = edata.get("tool") or "tool"
                payload = edata.get("payload")
                arg_str = json.dumps(payload, ensure_ascii=False) if isinstance(payload, (dict, list)) else str(payload or "")
                _emit({
                    "type": "tool",
                    "session_id": session_id,
                    "call_id": call_id,
                    "tool": tool_name,
                    "arguments": arg_str,
                })

        elif etype == "tool_call_finished":
            call_id = edata.get("id") or (list(seen_call_ids)[-1] if seen_call_ids else f"call_{len(completed_call_ids)+1}")
            if call_id not in completed_call_ids:
                completed_call_ids.add(call_id)
                tool_name = edata.get("tool") or ""
                raw_out = edata.get("output") or ""
                title = edata.get("title")
                clean_out = format_tool_output(tool_name, raw_out, title=title)
                _emit({
                    "type": "tool_result",
                    "session_id": session_id,
                    "call_id": call_id,
                    "tool": tool_name,
                    "output": clean_out,
                    "is_error": False,
                })

        elif etype == "web_search":
            queries = edata.get("queries", [])
            if queries:
                q_text = ", ".join(queries)
                _emit({"type": "reasoning", "session_id": session_id, "text": f"Searching: {q_text}", "thinking": True})

    try:
        async with WireTransport(bundle) as transport:
            result = await transport.send_turn(
                prompt=wire_prompt,
                conversation_id=sess.conversation_id,
                parent_id=sess.last_message_id,
                model_alias=args.model,
                on_event=on_event,
            )

        # Update session registry with newly completed turn
        if result and result.conversation_id:
            reg.update_turn(
                local_id=sess.local_id,
                conversation_id=result.conversation_id,
                last_message_id=result.message_id or "",
                model=args.model,
            )

        final_text = (result.text or "").strip()
        lifecycle_status = result.lifecycle_status or (
            "completed_turn" if result.status == "completed" else "transport_lost"
        )
        _emit({
            "type": "result",
            "session_id": session_id,
            "text": final_text,
            "finish_reason": lifecycle_status,
            "lifecycle_status": lifecycle_status,
        })
        return 0

    except Exception as exc:
        _emit({
            "type": "error",
            "session_id": session_id,
            "error": f"{type(exc).__name__}: {exc}",
        })
        print(f"GPT Wire Worker failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    try:
        return asyncio.run(run_worker())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
