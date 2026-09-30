import pytest
from starlette.testclient import TestClient
from pathlib import Path
from gpt_cli.server.app import create_app
from gpt_cli.session.registry import SessionRegistry
from gpt_cli.cli.main import build_cli_parser

def test_gateway_health():
    app = create_app()
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["model"] == "gpt-5-6-thinking"
    assert data["transport"] == "wire_conduit"

def test_gateway_models():
    app = create_app()
    client = TestClient(app)
    res = client.get("/v1/models")
    assert res.status_code == 200
    assert len(res.json()["data"]) >= 1

def test_gateway_messages_empty_validation():
    app = create_app()
    client = TestClient(app)
    res = client.post("/v1/messages", json={"model": "gpt-5-6-thinking", "messages": []})
    assert res.status_code == 400
    assert "Messages cannot be empty" in res.json()["error"]["message"]

def test_cli_parser_run_command():
    parser = build_cli_parser()
    args = parser.parse_args(["run", "-p", "Test prompt", "--json", "-s", "sess_custom", "--new"])
    assert args.command == "run"
    assert args.prompt == "Test prompt"
    assert args.json is True
    assert args.session == "sess_custom"
    assert args.new is True

def test_cli_parser_session_command():
    parser = build_cli_parser()
    args = parser.parse_args(["session", "list", "--json"])
    assert args.command == "session"
    assert args.session_action == "list"
    assert args.json is True

def test_cli_parser_gateway_command():
    parser = build_cli_parser()
    args = parser.parse_args(["start", "--port", "18000"])
    assert args.command == "start"
    assert args.port == 18000

def test_format_chat_messages_multi_turn():
    from gpt_cli.server.app import format_chat_messages
    messages = [
        {"role": "system", "content": "You are a helpful coding assistant."},
        {"role": "user", "content": "What is Python?"},
        {"role": "assistant", "content": "Python is a programming language."},
        {"role": "user", "content": "Show me a hello world example."}
    ]
    formatted = format_chat_messages(messages)
    assert "[System Instruction]" in formatted
    assert "You are a helpful coding assistant." in formatted
    assert "[User]\nWhat is Python?" in formatted
    assert "[Assistant]\nPython is a programming language." in formatted
    assert "[User]\nShow me a hello world example." in formatted

def test_format_chat_messages_with_tools_and_tool_results():
    from gpt_cli.server.app import format_chat_messages
    messages = [
        {"role": "user", "content": "mcp còn sống k"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "name": "health_check", "input": {"verbose": True}}
            ]
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "content": "{\"status\": \"ok\", \"uptime\": 16500}"}
            ]
        }
    ]
    tools = [
        {
            "name": "health_check",
            "description": "Check MCP server status",
            "input_schema": {"type": "object", "properties": {"verbose": {"type": "boolean"}}}
        }
    ]
    formatted = format_chat_messages(messages, tools=tools)
    assert "Available tools:" in formatted
    assert "- health_check: Check MCP server status" in formatted
    assert "[User]\nmcp còn sống k" in formatted
    assert "[Assistant]\n[Tool Call: health_check input={\"verbose\": true}]" in formatted
    assert "[Tool Output]: {\"status\": \"ok\", \"uptime\": 16500}" in formatted

def test_anthropic_messages_streaming_blocks(monkeypatch):
    import json
    from unittest.mock import AsyncMock
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.telemetry.events import TelemetryEvent

    # Mock TokenCacheManager
    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )

    async def fake_send_turn(self, prompt, *args, on_event=None, **kwargs):
        if on_event:
            # 1. Thinking
            on_event(TelemetryEvent(event_type="thinking", data={"thoughts": [{"summary": "Checking MCP"}], "title": "Health Check"}))
            # 2. Tool call
            on_event(TelemetryEvent(event_type="tool_call_started", data={"tool": "health_check", "payload": '{"check": "mcp"}'}))
            on_event(TelemetryEvent(event_type="tool_call_finished", data={"title": "Done"}))
            # 3. Final text
            on_event(TelemetryEvent(event_type="delta", data={"text": "MCP is active."}))
            # 4. Done
            on_event(TelemetryEvent(event_type="done", data={}))
        return TurnResult(text="MCP is active.", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    app = create_app()
    client = TestClient(app)
    res = client.post(
        "/v1/messages",
        json={
            "model": "deepseek-reasoner",
            "stream": True,
            "messages": [{"role": "user", "content": "mcp còn sống k"}]
        }
    )
    assert res.status_code == 200
    events = []
    for line in res.text.strip().split("\n\n"):
        line = line.strip()
        if not line:
            continue
        ev_name = None
        ev_data = None
        for sub in line.split("\n"):
            if sub.startswith("event:"):
                ev_name = sub.split(":", 1)[1].strip()
            elif sub.startswith("data:"):
                ev_data = json.loads(sub.split(":", 1)[1].strip())
        if ev_name and ev_data:
            events.append((ev_name, ev_data))

    ev_names = [e[0] for e in events]
    assert "message_start" in ev_names
    assert "content_block_start" in ev_names
    assert "content_block_delta" in ev_names
    assert "content_block_stop" in ev_names
    assert "message_delta" in ev_names
    assert "message_stop" in ev_names

    # Check block types
    starts = [e[1] for e in events if e[0] == "content_block_start"]
    block_types = [s["content_block"]["type"] for s in starts]
    assert "thinking" in block_types
    assert "tool_use" in block_types
    assert "text" in block_types

    # Verify tool_use arguments
    tool_delta = next(e[1] for e in events if e[0] == "content_block_delta" and e[1]["delta"].get("type") == "input_json_delta")
    assert json.loads(tool_delta["delta"]["partial_json"]) == {"check": "mcp"}

    # Verify message_delta has end_turn because text followed
    msg_delta = next(e[1] for e in events if e[0] == "message_delta")
    assert msg_delta["delta"]["stop_reason"] == "end_turn"

def test_anthropic_messages_streaming_waiting_tool_use(monkeypatch):
    import json
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.telemetry.events import TelemetryEvent

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )

    async def fake_send_turn(self, prompt, *args, on_event=None, **kwargs):
        if on_event:
            # Only tool call, no subsequent text
            on_event(TelemetryEvent(event_type="tool_call_started", data={"tool": "bash", "payload": '{"command": "uptime"}'}))
            on_event(TelemetryEvent(event_type="done", data={}))
        return TurnResult(text="", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    app = create_app()
    client = TestClient(app)
    res = client.post(
        "/v1/messages",
        json={
            "model": "gpt-5-6-thinking",
            "stream": True,
            "messages": [{"role": "user", "content": "check uptime"}]
        }
    )
    assert res.status_code == 200
    events = []
    for line in res.text.strip().split("\n\n"):
        line = line.strip()
        if not line:
            continue
        ev_name = None
        ev_data = None
        for sub in line.split("\n"):
            if sub.startswith("event:"):
                ev_name = sub.split(":", 1)[1].strip()
            elif sub.startswith("data:"):
                ev_data = json.loads(sub.split(":", 1)[1].strip())
        if ev_name and ev_data:
            events.append((ev_name, ev_data))

    msg_delta = next(e[1] for e in events if e[0] == "message_delta")
    assert msg_delta["delta"]["stop_reason"] == "tool_use"

def test_anthropic_messages_multi_turn_session_continuity(monkeypatch, tmp_path):
    import json
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.telemetry.events import TelemetryEvent
    from gpt_cli.session.registry import SessionRegistry

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )
    # Redirect session dir to tmp_path
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", str(tmp_path))

    call_history = []
    async def fake_send_turn(self, prompt, conversation_id=None, parent_id=None, *args, on_event=None, **kwargs):
        call_history.append({"prompt": prompt, "conv_id": conversation_id, "parent_id": parent_id})
        if on_event:
            on_event(TelemetryEvent(event_type="conversation_id", data={"conversation_id": "chatgpt_conv_99"}))
            on_event(TelemetryEvent(event_type="message_id", data={"message_id": f"msg_node_{len(call_history)}"}))
            on_event(TelemetryEvent(event_type="delta", data={"text": "Answer"}))
            on_event(TelemetryEvent(event_type="done", data={}))
        return TurnResult(text="Answer", conversation_id="chatgpt_conv_99", message_id=f"msg_node_{len(call_history)}", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    app = create_app()
    client = TestClient(app)

    # Turn 1
    res1 = client.post(
        "/v1/messages",
        headers={"x-deepseek-harness-session-id": "sess_unit_test"},
        json={
            "model": "deepseek-reasoner",
            "messages": [{"role": "user", "content": "Hello"}]
        }
    )
    assert res1.status_code == 200
    assert call_history[0]["conv_id"] is None
    assert call_history[0]["parent_id"] is None

    # Turn 2 with same session header and full history
    res2 = client.post(
        "/v1/messages",
        headers={"x-deepseek-harness-session-id": "sess_unit_test"},
        json={
            "model": "deepseek-reasoner",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "How are you?"}
            ]
        }
    )
    assert res2.status_code == 200
    assert call_history[1]["conv_id"] == "chatgpt_conv_99"
    assert call_history[1]["parent_id"] == "msg_node_1"
    # Prompt sent to ChatGPT should only be the latest user message
    assert "How are you?" in call_history[1]["prompt"]
    assert "Hello" not in call_history[1]["prompt"]


