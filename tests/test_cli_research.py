import argparse
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gpt_cli.cli.parser import build_cli_parser, parse_cli_args
from gpt_cli.cli.research_runner import _sanitize_tag, _build_phase_prompt, handle_research
from gpt_cli.core.types import TurnResult


def test_research_parser_defaults():
    parser = build_cli_parser()
    args = parser.parse_args(["research", "Async", "IO", "patterns"])
    assert args.command == "research"
    assert args.pos_prompt == ["Async", "IO", "patterns"]
    assert args.prompt is None
    assert args.deep is False
    assert args.turns is None
    assert args.report_file is None
    assert args.effort is None


def test_research_parser_deep_flag():
    parser = build_cli_parser()
    args = parser.parse_args(["research", "-p", "Memory leak in Python", "--deep", "--turns", "4", "-o", "/tmp/report.md"])
    assert args.command == "research"
    assert args.prompt == "Memory leak in Python"
    assert args.deep is True
    assert args.turns == 4
    assert args.report_file == "/tmp/report.md"


def test_sanitize_tag():
    assert _sanitize_tag("Async IO Performance!") == "research:async_io_performance"
    assert _sanitize_tag("   ") == "research:topic"


def test_build_phase_prompt_single_turn():
    p = _build_phase_prompt("Rust vs C++", 1, 1)
    assert "[TECHNICAL RESEARCH REPORT]" in p
    assert "Rust vs C++" in p


def test_build_phase_prompt_deep_phases():
    p1 = _build_phase_prompt("Distributed Consensus", 1, 3)
    assert "PHASE 1/3" in p1
    assert "Distributed Consensus" in p1

    p2 = _build_phase_prompt("Distributed Consensus", 2, 3)
    assert "PHASE 2/3" in p2

    p3 = _build_phase_prompt("Distributed Consensus", 3, 3)
    assert "FINAL SYNTHESIS" in p3


@pytest.mark.anyio
async def test_handle_research_single_turn(tmp_path):
    report_file = tmp_path / "report.md"
    args = build_cli_parser().parse_args([
        "research", "Profiling Python", "-o", str(report_file), "--json"
    ])

    mock_res = TurnResult(
        status="completed",
        text="# Python Profiling Report\n\nUse cProfile and py-spy.",
        model="gpt-5-6-thinking",
        duration_ms=1200,
        conversation_id="conv_123",
        message_id="msg_123",
    )
    mock_handle_run = AsyncMock(return_value=mock_res)

    with patch("gpt_cli.cli.research_runner.SessionRegistry") as mock_reg_cls:
        mock_reg = mock_reg_cls.return_value
        mock_reg.create_session.return_value.local_id = "sess_research_1"
        await handle_research(args, handle_run=mock_handle_run)

    assert mock_handle_run.call_count == 1
    assert report_file.exists()
    assert "Python Profiling Report" in report_file.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_handle_research_deep_multi_turn(tmp_path):
    report_file = tmp_path / "deep_report.md"
    args = build_cli_parser().parse_args([
        "research", "Kafka vs Pulsar", "--deep", "--turns", "2", "-o", str(report_file), "--json"
    ])

    results = [
        TurnResult(status="completed", text="Phase 1 findings", model="gpt-5-6-thinking", duration_ms=1000, conversation_id="conv_1", message_id="msg_1"),
        TurnResult(status="completed", text="# Definitive Kafka vs Pulsar Report\n\nThroughput comparison.", model="gpt-5-6-thinking", duration_ms=1500, conversation_id="conv_1", message_id="msg_2"),
    ]
    mock_handle_run = AsyncMock(side_effect=results)

    with patch("gpt_cli.cli.research_runner.SessionRegistry") as mock_reg_cls:
        mock_reg = mock_reg_cls.return_value
        mock_reg.create_session.return_value.local_id = "sess_research_2"
        await handle_research(args, handle_run=mock_handle_run)

    assert mock_handle_run.call_count == 2
    assert report_file.exists()
    assert "Definitive Kafka vs Pulsar Report" in report_file.read_text(encoding="utf-8")


@pytest.mark.anyio
async def test_handle_research_missing_prompt():
    args = build_cli_parser().parse_args(["research", "--json"])
    mock_handle_run = AsyncMock()
    with pytest.raises(SystemExit):
        await handle_research(args, handle_run=mock_handle_run)
    assert mock_handle_run.call_count == 0
