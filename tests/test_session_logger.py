import json
import pytest
from pathlib import Path
from gpt_cli.session.logger import SessionLogManager
from gpt_cli.telemetry.events import TelemetryEvent

def test_session_logger_turn_recording(tmp_path: Path):
    log_mgr = SessionLogManager(base_dir=tmp_path)
    session_id = "sess_test_123"

    # Log turn 1
    log_mgr.log_turn(
        local_id=session_id,
        turn_index=1,
        prompt="Hello world",
        response="Hello! How can I help you?",
        model="gpt-5-6-thinking",
        duration_ms=1200,
        conversation_id="conv_1",
        message_id="msg_1",
        thinking=["Considering user greeting", "Replying politely"]
    )

    # Check files created
    sess_dir = tmp_path / session_id
    assert sess_dir.exists()
    assert (sess_dir / "history.jsonl").exists()

    # Read history
    history = log_mgr.get_history(session_id)
    assert len(history) == 1
    assert history[0]["turn_index"] == 1
    assert history[0]["prompt"] == "Hello world"
    assert history[0]["response"] == "Hello! How can I help you?"
    assert history[0]["thinking"] == ["Considering user greeting", "Replying politely"]

    # Log turn 2
    log_mgr.log_turn(
        local_id=session_id,
        turn_index=2,
        prompt="Tell me a joke",
        response="Why did the robot cross the road?",
        model="gpt-5-6-thinking",
        duration_ms=1500,
        conversation_id="conv_1",
        message_id="msg_2",
    )

    history = log_mgr.get_history(session_id)
    assert len(history) == 2
    assert history[1]["turn_index"] == 2
    assert history[1]["prompt"] == "Tell me a joke"

def test_session_logger_events(tmp_path: Path):
    log_mgr = SessionLogManager(base_dir=tmp_path)
    session_id = "sess_ev_test"

    ev1 = TelemetryEvent(event_type="web_search", data={"queries": ["python docs"]})
    ev2 = TelemetryEvent(event_type="thinking", data={"text": "Searching web..."})

    log_mgr.log_event(session_id, ev1, turn_index=1)
    log_mgr.log_event(session_id, ev2, turn_index=1)

    events = log_mgr.get_events(session_id)
    assert len(events) == 2
    assert events[0]["event_type"] == "web_search"
    assert events[1]["event_type"] == "thinking"

def test_format_readable_log(tmp_path: Path):
    log_mgr = SessionLogManager(base_dir=tmp_path)
    session_id = "sess_fmt"
    log_mgr.log_turn(
        local_id=session_id,
        turn_index=1,
        prompt="Test question",
        response="Test answer",
        model="gpt-5-6-thinking",
        duration_ms=800,
        conversation_id="conv_1",
        message_id="msg_1"
    )
    formatted = log_mgr.format_readable_log(session_id)
    assert "TURN #1" in formatted
    assert "Test question" in formatted
    assert "Test answer" in formatted


def test_format_readable_log_appends_tool_durations_without_removing_payload_or_result(tmp_path: Path):
    log_mgr = SessionLogManager(base_dir=tmp_path)
    session_id = "sess_tool_duration"
    log_mgr.log_turn(
        local_id=session_id,
        turn_index=1,
        prompt="Test",
        response="Done",
        model="gpt-5-6-thinking",
        duration_ms=10,
        conversation_id="conv",
        message_id="msg",
        tool_calls=[{
            "tool": "terminal_execute",
            "payload": "rm -rf secret",
            "result": {"secret": "output"},
            "client_elapsed_ms": 0,
            "server_duration_ms": 12,
        }],
    )

    formatted = log_mgr.format_readable_log(session_id)
    assert "client 0 ms" in formatted
    assert "server 12 ms" in formatted
    assert "rm -rf secret" in formatted
    assert "secret" in formatted



def test_format_readable_log_preserves_tool_context_with_concise_timings_and_caps_results(tmp_path: Path):
    log_mgr = SessionLogManager(base_dir=tmp_path)
    session_id = "sess_tool_context"
    log_mgr.log_turn(
        local_id=session_id,
        turn_index=1,
        prompt="Test",
        response="Done",
        model="gpt-5-6-thinking",
        duration_ms=10,
        conversation_id="conv",
        message_id="msg",
        tool_calls=[{
            "tool": "terminal_execute",
            "payload": "echo hello",
            "result": {"output": "hello"},
            "client_elapsed_ms": 0,
            "server_duration_ms": 12,
        }],
    )

    formatted = log_mgr.format_readable_log(session_id)
    assert "echo hello" in formatted
    assert "hello" in formatted
    assert "client 0 ms" in formatted
    assert "server 12 ms" in formatted


def test_tool_timing_fields_remain_in_json_history(tmp_path: Path):
    log_mgr = SessionLogManager(base_dir=tmp_path)
    session_id = "sess_tool_json_timing"

    log_mgr.log_turn(
        local_id=session_id,
        turn_index=1,
        prompt="Test",
        response="Done",
        model="gpt-5-6-thinking",
        duration_ms=10,
        conversation_id="conv",
        message_id="msg",
        tool_calls=[{
            "tool": "terminal_execute",
            "payload": "echo hello",
            "result": {"output": "hello"},
            "client_elapsed_ms": 0,
            "server_duration_ms": 25,
        }],
    )

    raw = (tmp_path / session_id / "history.jsonl").read_text(encoding="utf-8")
    record = json.loads(raw.splitlines()[0])
    tool = record["tool_calls"][0]

    assert tool["client_elapsed_ms"] == 0
    assert tool["server_duration_ms"] == 25
    assert tool["result"] == {"output": "hello"}
