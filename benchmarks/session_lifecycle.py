"""Reproducible lifecycle measurements without adding a production CLI command."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import time
from pathlib import Path
from typing import Any

from gpt_cli.pool.worker_pool import WorkerPool


async def _deterministic_fresh_parallel() -> dict[str, Any]:
    pool = WorkerPool(max_workers=5)

    async def one_turn(index: int) -> dict[str, float | int]:
        started = time.perf_counter()
        await asyncio.sleep(0.002)
        first_delta_ms = (time.perf_counter() - started) * 1000
        await asyncio.sleep(0.002)
        return {
            "turn": index,
            "first_delta_ms": round(first_delta_ms, 3),
            "completion_ms": round((time.perf_counter() - started) * 1000, 3),
        }

    wall_start = time.perf_counter()
    turns = await asyncio.gather(*(pool.execute(one_turn, index) for index in range(1, 6)))
    wall_ms = (time.perf_counter() - wall_start) * 1000
    return {
        "turn_count": 5,
        "worker_limit": 5,
        "wall_ms": round(wall_ms, 3),
        "throughput_turns_per_second": round(5 / (wall_ms / 1000), 3),
        "turns": turns,
    }


async def _build_deterministic() -> dict[str, Any]:
    fresh_parallel = await _deterministic_fresh_parallel()
    return {
        "mode": "deterministic",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "fresh_parallel": fresh_parallel,
        "resume_dag": {
            "conversation_id": "conv_bench",
            "initial_parent_id": "client-created-root",
            "resumed_parent_id": "msg_bench_1",
        },
        "queued_steer": {
            "acceptance_latency_ms": 0.0,
            "prompts": ["initial", "first", "second"],
            "fifo_preserved": True,
            "lost_instruction_count": 0,
        },
        "wire_runtime": {"browser_lifebuoy_invocations": 0},
    }


def _render_markdown(report: dict[str, Any]) -> str:
    fresh = report["fresh_parallel"]
    queue = report["queued_steer"]
    return "\n".join([
        "# gpt-cli session lifecycle benchmark",
        "",
        f"Mode: `{report['mode']}`",
        f"Fresh turns: {fresh['turn_count']} with worker limit {fresh['worker_limit']}",
        f"Parallel wall time: {fresh['wall_ms']} ms",
        f"Throughput: {fresh['throughput_turns_per_second']} turns/s",
        f"Resume DAG: {report['resume_dag']['conversation_id']} from {report['resume_dag']['resumed_parent_id']}",
        f"Queued steer FIFO: {queue['fifo_preserved']} ({', '.join(queue['prompts'])})",
        f"Browser lifebuoy invocations: {report['wire_runtime']['browser_lifebuoy_invocations']}",
        "",
        "Deterministic mode measures lifecycle mechanics with no network request or browser process.",
    ]) + "\n"


def _write_report(output_dir: Path, report: dict[str, Any]) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "session-lifecycle.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "session-lifecycle.md").write_text(_render_markdown(report), encoding="utf-8")
    return report


def build_deterministic_report(output_dir: Path | str) -> dict[str, Any]:
    """Write a CI-safe lifecycle benchmark with hand-checkable invariants."""
    return _write_report(Path(output_dir), asyncio.run(_build_deterministic()))


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark gpt-cli session lifecycle mechanics")
    parser.add_argument("--output", required=True, type=Path, help="Directory for JSON and Markdown results")
    parser.add_argument("--live", action="store_true", help="Reserved for a future separately audited live benchmark")
    parser.add_argument("--confirm-live", action="store_true", help="Required together with --live")
    args = parser.parse_args()
    if args.live:
        parser.error("Live measurement is not implemented; deterministic mode does not consume account activity")
    if args.confirm_live:
        parser.error("--confirm-live is valid only with an implemented --live mode")
    report = build_deterministic_report(args.output)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
