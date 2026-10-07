"""Deep Research execution runner for gpt CLI.

Provides multi-turn iterative deep technical research with structured synthesis
and markdown report export.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from ..config.settings import Settings
from ..session.registry import SessionRegistry
from ..session.logger import SessionLogManager


def _sanitize_tag(topic: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]+", "_", topic.strip().lower())[:24].strip("_")
    return f"research:{cleaned or 'topic'}"


def _build_phase_prompt(topic: str, phase_idx: int, total_phases: int) -> str:
    if total_phases <= 1:
        return (
            f"[TECHNICAL RESEARCH REPORT]\n"
            f"Topic: {topic}\n\n"
            "Perform exhaustive, state-of-the-art technical research on this topic. "
            "Examine architecture patterns, low-level mechanics, algorithmic trade-offs, "
            "edge cases, empirical benchmarks, and production-tested patterns. "
            "Provide concrete code examples, mathematical explanations where relevant, "
            "and actionable recommendations."
        )

    if phase_idx == 1:
        return (
            f"[DEEP RESEARCH - PHASE 1/{total_phases}: PROBLEM DECOMPOSITION & LANDSCAPE SURVEY]\n"
            f"Topic: {topic}\n\n"
            "1. Deconstruct the problem space into fundamental technical dimensions.\n"
            "2. Survey state-of-the-art architectures, industry RFCs/standards, and existing solutions.\n"
            "3. Identify primary performance bottlenecks, security implications, and edge cases.\n"
            "4. Formulate specific technical hypotheses and open trade-offs to rigorously verify in subsequent phases."
        )
    elif phase_idx < total_phases:
        return (
            f"[DEEP RESEARCH - PHASE {phase_idx}/{total_phases}: TECHNICAL DEEP-DIVE & BENCHMARK VERIFICATION]\n"
            "Focus deeply on the technical mechanics and unresolved questions from the previous phase:\n"
            "1. Analyze low-level implementations, data structures, and algorithmic complexity.\n"
            "2. Compare alternatives with concrete metrics (latency, memory, concurrency overhead, failure recovery).\n"
            "3. Provide realistic code snippets or configuration patterns demonstrating the optimal approach.\n"
            "4. Rigorously critique potential failure modes and stress scenarios."
        )
    else:
        return (
            f"[DEEP RESEARCH - FINAL SYNTHESIS: DEFINITIVE TECHNICAL SPECIFICATION]\n"
            "Synthesize all findings across phases into a definitive, publication-quality technical report:\n"
            "1. Executive Summary & Recommended Architecture Decision Record (ADR)\n"
            "2. Full Technical Blueprint with concrete code & specifications\n"
            "3. Empirical Performance Analysis & Trade-off Matrix\n"
            "4. Robustness, Error Handling & Security Guidelines\n"
            "5. Implementation Checklist & Verification Strategy."
        )


async def handle_research(args: argparse.Namespace, *, handle_run) -> None:
    """Execute deep research across one or multiple iterative turns."""
    # Resolve prompt from -p, pos_prompt, or stdin
    raw_prompt = getattr(args, "prompt", None)
    pos_parts = getattr(args, "pos_prompt", [])
    if not raw_prompt and pos_parts:
        raw_prompt = " ".join(pos_parts).strip()
    if not raw_prompt and hasattr(sys.stdin, "isatty"):
        try:
            if not sys.stdin.isatty():
                raw_prompt = sys.stdin.read().strip()
        except (OSError, ValueError):
            pass

    if not raw_prompt:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "message": "Research prompt or topic is required"}))
        else:
            print("Error: Research prompt or topic is required. Example: `gpt research \"Async IO optimization\" --deep`", file=sys.stderr)
        raise SystemExit(2)

    is_deep = bool(getattr(args, "deep", False))
    total_turns = getattr(args, "turns", None)
    if total_turns is None:
        total_turns = 3 if is_deep else 1
    elif total_turns <= 0:
        raise ValueError("--turns must be greater than zero")

    settings = Settings()
    effort = getattr(args, "effort", None)
    if not effort:
        effort = "extended" if is_deep else settings.default_effort

    registry = SessionRegistry()
    explicit_session_id = getattr(args, "resume_session", None)
    if explicit_session_id:
        session = registry.require_session(explicit_session_id)
    elif getattr(args, "resume", False):
        session = registry.require_session(None)
    elif not getattr(args, "conversation", None):
        tag = getattr(args, "tag", None) or _sanitize_tag(raw_prompt)
        session = registry.create_session(tag=tag)
    else:
        session = None

    resolved_session_id: str | None = session.local_id if session else None

    def pin_resolved_session(local_id: str) -> None:
        nonlocal resolved_session_id
        resolved_session_id = local_id

    is_json = bool(getattr(args, "json", False))
    report_file = getattr(args, "report_file", None)

    if not is_json:
        mode_label = "Deep Iterative Research" if is_deep else "Standard Research"
        sys.stdout.write(f"\n🔬 \033[1mStarting {mode_label}\033[0m: \"{raw_prompt}\"\n")
        sys.stdout.write(f"   Target Iterations: {total_turns} | Effort: {effort} | Model: {getattr(args, 'model', settings.default_model)}\n\n")
        sys.stdout.flush()

    turn_results = []
    latest_text = ""
    latest_result = None

    for turn_idx in range(1, total_turns + 1):
        phase_prompt = _build_phase_prompt(raw_prompt, turn_idx, total_turns)

        turn_args = argparse.Namespace(**vars(args))
        turn_args.command = "run"
        turn_args.prompt = phase_prompt
        turn_args.pos_prompt = []
        turn_args.effort = effort
        turn_args.resume = False
        if resolved_session_id:
            turn_args.resume_session = resolved_session_id
            turn_args.conversation = None

        if is_json:
            turn_args.quiet = True

        if not is_json and total_turns > 1:
            sys.stdout.write(f"\033[36m--- Phase {turn_idx}/{total_turns} ---\033[0m\n\n")
            sys.stdout.flush()

        res = await handle_run(turn_args, on_session_resolved=pin_resolved_session)
        latest_result = res
        if res:
            latest_text = res.text or latest_text
            turn_results.append({
                "phase": turn_idx,
                "text": res.text,
                "duration_ms": res.duration_ms,
                "status": res.status,
                "error": res.error,
            })

        if res is None or res.status not in {"completed", "terminal"} or res.error:
            if not is_json:
                sys.stdout.write(f"\n⚠️ Research halted early at phase {turn_idx}: {res.error if res else 'Unknown error'}\n")
                sys.stdout.flush()
            break

    # Save report if requested
    report_saved_path = None
    if report_file and latest_text:
        try:
            p = Path(report_file).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(latest_text, encoding="utf-8")
            report_saved_path = str(p)
            if not is_json:
                sys.stdout.write(f"\n📝 \033[32mResearch report successfully saved to:\033[0m {p}\n")
                sys.stdout.flush()
        except Exception as e:
            if not is_json:
                sys.stderr.write(f"\n⚠️ Failed to save report to {report_file}: {e}\n")

    if is_json:
        sess_id = resolved_session_id or (session.local_id if session else "")
        print(json.dumps({
            "status": "ok" if latest_result and not latest_result.error else "incomplete",
            "session_id": sess_id,
            "topic": raw_prompt,
            "deep": is_deep,
            "total_turns": total_turns,
            "turns_completed": len(turn_results),
            "report_file": report_saved_path,
            "final_report": latest_text,
            "phases": turn_results,
        }, ensure_ascii=False))
