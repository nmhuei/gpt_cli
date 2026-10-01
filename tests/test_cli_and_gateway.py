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


def test_cli_json_reports_model_terminal_without_claiming_success(monkeypatch, tmp_path, capsys):
    import asyncio
    import json
    from gpt_cli.cli.main import handle_run
    from gpt_cli.core.types import TokenBundle, TurnResult

    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")

    async def fake_send_turn(self, *args, **kwargs):
        return TurnResult(
            text="I could not complete the task.",
            conversation_id="conv_terminal",
            message_id="msg_terminal",
            model="gpt-5-6-thinking",
            lifecycle_status="model_terminal",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["run", "--json", "--new", "solve /tmp/challenge"])
    asyncio.run(handle_run(args))

    output = json.loads(capsys.readouterr().out)
    assert output["lifecycle_status"] == "model_terminal"
    assert output["status"] == "terminal"


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

def test_format_chat_messages_single_user_with_system_and_tools():
    from gpt_cli.server.app import format_chat_messages
    messages = [{"role": "user", "content": "Kiểm tra hệ thống"}]
    tools = [{"name": "bash", "description": "Run shell", "input_schema": {"type": "object"}}]
    formatted = format_chat_messages(
        messages,
        system_prompt="You are an autonomous CTF solver.",
        tools=tools,
    )
    assert "[System Instruction]" in formatted
    assert "You are an autonomous CTF solver." in formatted
    assert "Available tools:" in formatted
    assert "- bash: Run shell" in formatted
    assert "[User]\nKiểm tra hệ thống" in formatted

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

    # Turn 3 with x-claude-code-session-id
    res3 = client.post(
        "/v1/messages",
        headers={"x-claude-code-session-id": "claude_unit_test"},
        json={
            "model": "gpt-5-6-thinking",
            "messages": [{"role": "user", "content": "First message"}]
        }
    )
    assert res3.status_code == 200
    assert call_history[2]["conv_id"] is None

    # Turn 4 continuation with x-claude-code-session-id
    res4 = client.post(
        "/v1/messages",
        headers={"x-claude-code-session-id": "claude_unit_test"},
        json={
            "model": "gpt-5-6-thinking",
            "messages": [
                {"role": "user", "content": "First message"},
                {"role": "assistant", "content": "Answer"},
                {"role": "user", "content": "Second message"}
            ]
        }
    )
    assert res4.status_code == 200
    assert call_history[3]["conv_id"] == "chatgpt_conv_99"
    assert call_history[3]["parent_id"] == "msg_node_3"
    assert "Second message" in call_history[3]["prompt"]
    assert "First message" not in call_history[3]["prompt"]

def test_resolve_tool_name_mcp_namespacing():
    from gpt_cli.server.app import _resolve_tool_name
    registered = {"bash", "glob", "read", "mcp__br__health_check", "mcp__br__host_run_command"}

    res, unwrap = _resolve_tool_name("api_tool.call_tool (health_check)", registered)
    assert res == "mcp__br__health_check"
    assert unwrap is True

    res, unwrap = _resolve_tool_name("health_check", registered)
    assert res == "mcp__br__health_check"
    assert unwrap is False

    res, unwrap = _resolve_tool_name("functions.exec", registered)
    assert res == "functions.exec"
    assert unwrap is False

    res, unwrap = _resolve_tool_name("host_list_directory", registered)
    assert res == "host_list_directory"
    assert unwrap is False

def test_normalize_tool_payload_bash_and_pruning():
    from gpt_cli.server.app import _normalize_tool_payload

    # Test Bash fallback from resource list with paths
    raw = {"paths": ["/var/log", "/tmp"], "query": "noop"}
    norm = _normalize_tool_payload("Bash", raw)
    assert norm["command"] == "ls -la /var/log /tmp"
    assert norm["description"] == "Run: ls -la /var/log /tmp"
    assert "paths" not in norm
    assert "query" not in norm

    # Test Bash empty command safety
    norm_empty = _normalize_tool_payload("Bash", {})
    assert norm_empty["command"] == ""
    assert norm_empty["description"] == "Execute command"

    # Test Read dual path normalization
    raw_read = {"file": "/etc/hosts"}
    norm_read = _normalize_tool_payload("Read", raw_read)
    assert norm_read["path"] == "/etc/hosts"
    assert norm_read["file_path"] == "/etc/hosts"
    assert "file" not in norm_read

def test_gateway_compaction_isolation(monkeypatch, tmp_path):
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.telemetry.events import TelemetryEvent
    from gpt_cli.session.registry import SessionRegistry

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", str(tmp_path))

    call_history = []
    async def fake_send_turn(self, prompt, conversation_id=None, parent_id=None, *args, on_event=None, **kwargs):
        call_history.append({"prompt": prompt, "conv_id": conversation_id, "parent_id": parent_id})
        return TurnResult(text="Summary of history", conversation_id="conv_compaction", message_id="msg_compaction", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    app = create_app()
    client = TestClient(app)

    # Pre-create session
    reg = SessionRegistry()
    sess = reg.get_or_create(session_id="sess_compact_test")
    reg.update_turn("sess_compact_test", conversation_id="conv_main", last_message_id="msg_main_node")

    # Send request with metadata.purpose = compaction
    res = client.post(
        "/v1/messages",
        headers={"x-deepseek-harness-session-id": "sess_compact_test"},
        json={
            "model": "gpt-5-6-thinking",
            "metadata": {"purpose": "compaction"},
            "messages": [
                {"role": "user", "content": "Summarize this"}
            ]
        }
    )
    assert res.status_code == 200
    # Wire call should NOT attach to main conversation
    assert call_history[0]["conv_id"] is None
    assert call_history[0]["parent_id"] is None

    # Session in registry should still point to original main node!
    refreshed = reg.get_session("sess_compact_test")
    assert refreshed.last_message_id == "msg_main_node"
    assert refreshed.conversation_id == "conv_main"

def test_gateway_prefix_diffing_preserves_multiple_tool_results(monkeypatch, tmp_path):
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.telemetry.events import TelemetryEvent
    from gpt_cli.session.registry import SessionRegistry

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", str(tmp_path))

    call_history = []
    async def fake_send_turn(self, prompt, conversation_id=None, parent_id=None, *args, on_event=None, **kwargs):
        call_history.append({"prompt": prompt, "conv_id": conversation_id, "parent_id": parent_id})
        return TurnResult(text="Processed results", conversation_id="conv_multi", message_id="msg_multi_2", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    app = create_app()
    client = TestClient(app)

    reg = SessionRegistry()
    reg.get_or_create(session_id="sess_multi_tool")
    reg.update_turn("sess_multi_tool", conversation_id="conv_multi", last_message_id="msg_multi_1")

    # Messages contain: user, assistant, then TWO tool_result blocks
    messages = [
        {"role": "user", "content": "Initial prompt"},
        {"role": "assistant", "content": "Running two tools"},
        {"role": "user", "content": [{"type": "tool_result", "content": "RESULT_FIRST", "tool_use_id": "call_1"}]},
        {"role": "user", "content": [{"type": "tool_result", "content": "RESULT_SECOND", "tool_use_id": "call_2"}]}
    ]

    res = client.post(
        "/v1/messages",
        headers={"x-deepseek-harness-session-id": "sess_multi_tool"},
        json={
            "model": "gpt-5-6-thinking",
            "messages": messages
        }
    )
    assert res.status_code == 200
    sent_prompt = call_history[0]["prompt"]
    # Prefix diffing MUST preserve BOTH RESULT_FIRST and RESULT_SECOND!
    assert "RESULT_FIRST" in sent_prompt
    assert "RESULT_SECOND" in sent_prompt
    # And older turns before assistant should be trimmed
    assert "Initial prompt" not in sent_prompt

def test_anthropic_non_streaming_stop_reason_end_turn_when_text_produced(monkeypatch):
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.telemetry.events import TelemetryEvent

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )

    async def fake_send_turn(self, prompt, *args, on_event=None, **kwargs):
        if on_event:
            on_event(TelemetryEvent(event_type="tool_call_started", data={"tool": "health_check", "payload": "{}"}))
            on_event(TelemetryEvent(event_type="tool_call_finished", data={"output": "ok"}))
            on_event(TelemetryEvent(event_type="delta", data={"text": "Everything is healthy."}))
            on_event(TelemetryEvent(event_type="done", data={}))
        return TurnResult(text="Everything is healthy.", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    app = create_app()
    client = TestClient(app)

    res = client.post(
        "/v1/messages",
        json={
            "model": "gpt-5-6-thinking",
            "messages": [{"role": "user", "content": "status"}]
        }
    )
    assert res.status_code == 200
    data = res.json()
    # When text is produced, stop_reason MUST be end_turn even if tools were captured upstream
    assert data["stop_reason"] == "end_turn"
    assert any(b.get("type") == "text" for b in data["content"])

def test_cli_parser_newsession_command():
    from gpt_cli.cli.main import build_cli_parser
    parser = build_cli_parser()

    # Test top-level newsession
    args = parser.parse_args(["newsession", "-t", "test-tag", "Hello world"])
    assert args.command == "newsession"
    assert args.tag == "test-tag"
    assert args.pos_prompt == ["Hello world"]

    # Test top-level new
    args2 = parser.parse_args(["new", "-t", "test-tag-2"])
    assert args2.command == "new"
    assert args2.tag == "test-tag-2"

    # Test session new
    args3 = parser.parse_args(["session", "new", "-t", "sub-tag"])
    assert args3.command == "session"
    assert args3.session_action == "new"
    assert args3.tag == "sub-tag"


