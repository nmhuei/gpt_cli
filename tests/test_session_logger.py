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
