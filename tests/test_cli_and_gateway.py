import pytest
from starlette.testclient import TestClient
from pathlib import Path
from gpt_cli.server.app import create_app
from gpt_cli.session.registry import SessionRegistry
from gpt_cli.cli.main import build_cli_parser, parse_cli_args

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

def test_gateway_chat_completion_error_shape_for_empty_messages():
    app = create_app()
    client = TestClient(app)
    res = client.post("/v1/chat/completions", json={"model": "gpt-5-6-thinking", "messages": []})
    assert res.status_code == 400
    body = res.json()
    assert set(body) == {"error"}
    assert body["error"]["message"] == "Messages cannot be empty"


def test_gateway_anthropic_error_shape_for_invalid_json():
    app = create_app()
    client = TestClient(app)
    res = client.post("/v1/messages", content="{invalid", headers={"content-type": "application/json"})
    assert res.status_code == 400
    body = res.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert "Invalid JSON" in body["error"]["message"]


def test_gateway_chat_rejects_malformed_message_shape():
    client = TestClient(create_app())
    res = client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5-6-thinking", "messages": [{"content": "missing role"}]},
    )
    assert res.status_code == 400
    assert res.json()["error"]["message"] == "Messages must contain role and content"


def test_gateway_anthropic_rejects_non_object_message_entry():
    client = TestClient(create_app())
    res = client.post(
        "/v1/messages",
        json={"model": "gpt-5-6-thinking", "messages": ["bad"]},
    )
    assert res.status_code == 400
    payload = res.json()
    assert payload["type"] == "error"
    assert payload["error"]["type"] == "invalid_request_error"


def test_cli_parser_run_command():
    parser = build_cli_parser()
    args = parser.parse_args(["run", "-p", "Test prompt", "--json", "--resume-session", "sess_custom"])
    assert args.command == "run"
    assert args.prompt == "Test prompt"
    assert args.json is True
    assert args.resume_session == "sess_custom"


def test_cli_parser_agent_defaults_to_a_thirty_minute_continuous_session():
    parser = build_cli_parser()

    args = parser.parse_args(["agent", "-b", "br", "solve /tmp/challenge"])

    assert args.command == "agent"
    assert args.min_runtime_minutes == 30.0
    assert args.bridge == "br"


def test_agent_continues_from_the_same_dag_until_runtime_budget(monkeypatch, tmp_path, capsys):
    """A short completed turn is not permission for an autonomous agent to stop."""
    import asyncio
    from gpt_cli.cli.main import handle_agent
    from gpt_cli.core.types import TokenBundle, TurnResult

    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    calls: list[tuple[str, str | None, str | None]] = []

    async def fake_send_turn(self, prompt, conversation_id=None, parent_id=None, **kwargs):
        calls.append((prompt, conversation_id, parent_id))
        idx = len(calls)
        return TurnResult(
            text=f"turn-{idx}",
            conversation_id="conv_agent",
            message_id=f"msg_{idx}",
            model="gpt-5-6-thinking",
            duration_ms=100,
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args([
        "agent", "--min-runtime-minutes", "0.003", "solve /tmp/challenge",
    ])
    asyncio.run(handle_agent(args))

    assert [call[0].split()[-1] for call in calls] == ["/tmp/challenge", "continue"]
    assert calls[0][1:] == (None, None)
    assert calls[1][1:] == ("conv_agent", "msg_1")
    assert "Agent runtime target reached" in capsys.readouterr().out


def test_agent_json_emits_one_final_runtime_document(monkeypatch, tmp_path, capsys):
    import asyncio
    import json
    from gpt_cli.cli.main import handle_agent
    from gpt_cli.core.types import TokenBundle, TurnResult

    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    call_count = 0

    async def fake_send_turn(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return TurnResult(
            text="hidden from JSON stdout",
            conversation_id="conv_agent",
            message_id=f"msg_{call_count}",
            model="gpt-5-6-thinking",
            duration_ms=100,
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args([
        "agent", "--json", "--min-runtime-minutes", "0.003", "solve /tmp/challenge",
    ])
    asyncio.run(handle_agent(args))

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["status"] == "runtime_target_reached"


def test_cli_parser_accepts_bridge_prompt_shortcut_without_run_command():
    args = parse_cli_args(["-b", "br", "-p", "solve /tmp/challenge"])

    assert args.command == "run"
    assert args.bridge == "br"
    assert args.prompt == "solve /tmp/challenge"
    assert args.resume is False


def test_cli_parser_makes_new_and_resume_contract_unambiguous():
    """Restoring old implicit-session flags would reintroduce cross-task contamination."""
    parser = build_cli_parser()

    assert parser.parse_args(["run", "fresh work"]).resume is False
    assert parser.parse_args(["run", "--resume", "continue"]).resume is True
    assert parser.parse_args(["steer", "--resume-session", "sess_a", "check tests"]).command == "steer"
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--new", "x"])
    with pytest.raises(SystemExit):
        parser.parse_args(["new", "x"])
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--resume", "--conversation", "web_uuid", "x"])

def test_cli_parser_session_command():
    parser = build_cli_parser()
    args = parser.parse_args(["session", "list", "--json"])
    assert args.command == "session"
    assert args.session_action == "list"
    assert args.json is True


def test_session_new_json_emits_the_durable_local_id(monkeypatch, tmp_path, capsys):
    """An external scheduler must know the ID before it starts a worker."""
    from argparse import Namespace
    from gpt_cli.cli.main import handle_session

    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")

    handle_session(Namespace(session_action="new", tag="ctf:crypto:1", json=True))

    created = __import__("json").loads(capsys.readouterr().out)
    assert created["session_id"].startswith("sess_")
    assert created["tag"] == "ctf:crypto:1"
    assert created["turns"] == 0
    assert SessionRegistry(tmp_path / "sessions").require_session(created["session_id"]).tag == "ctf:crypto:1"

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
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    async def fake_send_turn(self, *args, **kwargs):
        return TurnResult(
            text="I could not complete the task.",
            conversation_id="conv_terminal",
            message_id="msg_terminal",
            model="gpt-5-6-thinking",
            lifecycle_status="model_terminal",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["run", "--json", "solve /tmp/challenge"])
    asyncio.run(handle_run(args))

    rendered = capsys.readouterr().out
    assert len(rendered.splitlines()) == 1
    output = json.loads(rendered)
    assert output["lifecycle_status"] == "model_terminal"
    assert output["status"] == "terminal"


def test_cli_json_is_one_document_when_telemetry_arrives(monkeypatch, tmp_path, capsys):
    """Progress belongs to events.jsonl; JSON stdout must remain parseable."""
    import asyncio
    import json
    from gpt_cli.cli.main import handle_run
    from gpt_cli.core.types import TokenBundle, TurnResult
    from gpt_cli.telemetry.events import TelemetryEvent

    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    async def fake_send_turn(self, *args, on_event=None, **kwargs):
        assert on_event is not None
        on_event(TelemetryEvent("recovery_started", {"source": "dag", "reason": "sse_interrupted"}))
        on_event(TelemetryEvent("tool_call_started", {"id": "tool_1", "tool": "health_check", "payload": "{}", "source": "dag"}))
        on_event(TelemetryEvent("tool_call_finished", {"id": "tool_1", "tool": "health_check", "output": "ok", "source": "dag"}))
        on_event(TelemetryEvent("thinking", {"thoughts": [{"summary": "Checking health"}], "source": "dag"}))
        return TurnResult(text="healthy", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["run", "--json", "status"])
    asyncio.run(handle_run(args))

    rendered = capsys.readouterr().out
    assert len(rendered.splitlines()) == 1
    output = json.loads(rendered)
    assert output["text"] == "healthy"
    assert output["thinking"] == ["Checking health"]
    assert output["tool_calls"] == [{"id": "tool_1", "tool": "health_check", "payload": "{}", "source": "dag"}]



def test_wire_payload_keeps_selected_mcp_source_in_prepare_and_message_metadata():
    from gpt_cli.transport.wire import WireTransport
    from gpt_cli.core.types import TokenBundle

    transport = WireTransport.__new__(WireTransport)
    payload = transport.build_payload(
        prompt="health",
        model_alias="gpt-5-6-thinking",
        selected_mcp_sources=[{"id": "connector_br", "name": "br"}],
    )

    assert payload["messages"][0]["metadata"]["selected_mcp_sources"] == [
        {"id": "connector_br", "name": "br"}
    ]

def test_cli_explicit_bridge_selects_registered_mcp_connector(monkeypatch, tmp_path):
    import asyncio
    from gpt_cli.cli.main import handle_run
    from gpt_cli.core.types import TokenBundle, TurnResult

    observed: dict[str, object] = {}
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(
        "gpt_cli.cli.main.resolve_connector_context",
        lambda name: (["connector_br"], [{"id": "connector_br", "name": "br"}]),
    )

    async def fake_send_turn(self, *args, **kwargs):
        observed["system_hints"] = kwargs.get("system_hints")
        observed["selected_mcp_sources"] = kwargs.get("selected_mcp_sources")
        return TurnResult(text="done", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["run", "--json", "-b", "br", "bind Chrono_II"])
    asyncio.run(handle_run(args))

    assert observed["system_hints"] == ["connector_br"]
    assert observed["selected_mcp_sources"] == [{"id": "connector_br", "name": "br"}]


def test_cli_leading_space_before_explicit_bridge_still_attaches_connector(monkeypatch, tmp_path):
    """Prompt normalization must not remove the only evidence for attachment."""
    import asyncio
    from gpt_cli.cli.main import handle_run
    from gpt_cli.core.types import TokenBundle, TurnResult

    observed: dict[str, object] = {}
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(
        "gpt_cli.cli.main.resolve_connector_context",
        lambda name: (["connector_br"], [{"id": "connector_br", "name": name}]),
    )

    async def fake_send_turn(self, prompt, *args, **kwargs):
        observed["prompt"] = prompt
        observed["selected_mcp_sources"] = kwargs.get("selected_mcp_sources")
        return TurnResult(text="done", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["run", "--json", " @br check health"])
    asyncio.run(handle_run(args))

    assert observed["prompt"] == "[$br](app://connector_br) check health"
    assert observed["selected_mcp_sources"] == [{"id": "connector_br", "name": "br"}]


def test_cli_named_resume_never_creates_a_missing_session(monkeypatch, tmp_path):
    """Falling back to get_or_create here would silently join unrelated work."""
    import asyncio
    from gpt_cli.cli.main import handle_run
    from gpt_cli.core.types import TokenBundle
    from gpt_cli.session.registry import SessionLookupError

    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")
    args = build_cli_parser().parse_args(["run", "--resume-session", "sess_missing", "continue"])

    with pytest.raises(SessionLookupError):
        asyncio.run(handle_run(args))

    assert SessionRegistry(tmp_path / "sessions").list_sessions() == []


def test_cli_steer_routes_named_live_session_to_its_control_socket(monkeypatch, tmp_path, capsys):
    """Changing CLI target resolution must not steer whichever turn happens to be newest."""
    import asyncio
    import json
    from gpt_cli.cli.main import handle_steer
    from gpt_cli.session.live_turns import LiveTurnRegistry, TurnOwner
    from gpt_cli.session.turn_control import TurnControlServer

    async def run() -> None:
        sessions = tmp_path / "sessions"
        runtime = tmp_path / "runtime"
        monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", sessions)
        monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: runtime)
        registry = SessionRegistry(sessions)
        registry.create_session(local_id="sess_one")
        live_registry = LiveTurnRegistry(runtime)
        owner = TurnOwner.create_for_current_process("sess_one", runtime / "turn.sock")
        live_registry.claim(owner)

        async def on_steer(request_id, prompt):
            return {"status": "queued", "session_id": "sess_one", "message": prompt}

        server = TurnControlServer(owner, live_registry, on_steer)
        await server.start()
        try:
            args = build_cli_parser().parse_args(["steer", "--resume-session", "sess_one", "--json", "check logs"])
            await handle_steer(args)
        finally:
            await server.close()
            live_registry.release("sess_one", owner.owner_id)

    asyncio.run(run())
    assert json.loads(capsys.readouterr().out) == {
        "status": "queued", "session_id": "sess_one", "message": "check logs"
    }


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

def test_cli_parser_keeps_session_new_as_the_only_metadata_creator():
    parser = build_cli_parser()
    args = parser.parse_args(["session", "new", "-t", "sub-tag"])
    assert args.command == "session"
    assert args.session_action == "new"
    assert args.tag == "sub-tag"



def test_sse_queue_overflow_aborts_instead_of_dropping_frames():
    import asyncio

    from gpt_cli.server.app import SSEQueueOverflow, _safe_queue_put

    queue = asyncio.Queue(maxsize=1)
    queue.put_nowait("first")

    try:
        _safe_queue_put(queue, "second")
    except SSEQueueOverflow:
        pass
    else:
        raise AssertionError("overflow must abort rather than drop SSE frames")

    assert queue.get_nowait() == "first"


def test_safe_queue_put_preserves_order_when_capacity_exists():
    import asyncio

    from gpt_cli.server.app import _safe_queue_put

    queue = asyncio.Queue(maxsize=3)
    for frame in ["one", "two", "three"]:
        _safe_queue_put(queue, frame)

    assert [queue.get_nowait(), queue.get_nowait(), queue.get_nowait()] == ["one", "two", "three"]


def test_chat_completions_sse_overflow_terminates_with_error(monkeypatch):
    from gpt_cli.core.types import TokenBundle
    from gpt_cli.server import app as server_app
    from gpt_cli.telemetry.events import TelemetryEvent

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )

    async def fake_send_turn(self, prompt, *args, on_event=None, **kwargs):
        on_event(TelemetryEvent(event_type="delta", data={"text": "first-frame"}))
        on_event(TelemetryEvent(event_type="delta", data={"text": "overflow"}))

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    original = server_app._safe_queue_put
    calls = {"n": 0}

    def overflow_after_first(queue, item):
        calls["n"] += 1
        if calls["n"] > 1:
            raise server_app.SSEQueueOverflow("forced")
        original(queue, item)

    monkeypatch.setattr(server_app, "_safe_queue_put", overflow_after_first)

    res = TestClient(create_app()).post(
        "/v1/chat/completions",
        json={"model": "gpt-5-6-thinking", "stream": True, "messages": [{"role": "user", "content": "test"}]},
    )
    assert res.status_code == 200
    assert "first-frame" in res.text
    assert "SSE queue overflow" in res.text
    assert res.text.index("first-frame") < res.text.index("SSE queue overflow")


def test_anthropic_sse_overflow_terminates_with_error(monkeypatch):
    from gpt_cli.core.types import TokenBundle
    from gpt_cli.server import app as server_app
    from gpt_cli.telemetry.events import TelemetryEvent

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )

    async def fake_send_turn(self, prompt, *args, on_event=None, **kwargs):
        on_event(TelemetryEvent(event_type="thinking", data={"thoughts": [{"summary": "first-frame"}]}))
        on_event(TelemetryEvent(event_type="thinking", data={"thoughts": [{"summary": "overflow"}]}))

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", fake_send_turn)

    original = server_app._safe_queue_put
    calls = {"n": 0}

    def overflow_after_first(queue, item):
        calls["n"] += 1
        if calls["n"] > 3:
            raise server_app.SSEQueueOverflow("forced")
        original(queue, item)

    monkeypatch.setattr(server_app, "_safe_queue_put", overflow_after_first)

    res = TestClient(create_app()).post(
        "/v1/messages",
        json={"model": "gpt-5-6-thinking", "stream": True, "messages": [{"role": "user", "content": "test"}]},
    )
    assert res.status_code == 200
    assert "first-frame" in res.text
    assert "SSE queue overflow" in res.text



def test_sse_overflow_preserves_queued_frame_before_error():
    import asyncio

    from gpt_cli.server.app import SSEQueueOverflow, _safe_queue_put

    queue = asyncio.Queue(maxsize=1)
    _safe_queue_put(queue, "first-frame")

    try:
        _safe_queue_put(queue, "second-frame")
    except SSEQueueOverflow:
        pass

    assert queue.get_nowait() == "first-frame"


def test_gateway_openai_rejects_top_level_json_array():
    client = TestClient(create_app())
    res = client.post("/v1/chat/completions", json=[])
    assert res.status_code == 400
    body = res.json()
    assert set(body) == {"error"}
    assert body["error"]["message"] == "Request body must be an object"


def test_gateway_anthropic_rejects_top_level_json_array():
    client = TestClient(create_app())
    res = client.post("/v1/messages", json=[])
    assert res.status_code == 400
    body = res.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["message"] == "Request body must be an object"


def test_sse_sentinel_waits_when_queue_has_space():
    import asyncio

    from gpt_cli.server.app import _finish_sse_worker

    async def run():
        queue = asyncio.Queue(maxsize=1)
        await _finish_sse_worker(queue)
        assert queue.get_nowait() is None

    asyncio.run(run())


def test_sse_sentinel_is_skipped_only_when_worker_cancelling():
    import asyncio

    from gpt_cli.server.app import _finish_sse_worker

    async def run():
        queue = asyncio.Queue(maxsize=1)

        async def cancelled_worker():
            asyncio.current_task().cancel()
            try:
                await asyncio.sleep(0)
            except asyncio.CancelledError:
                await _finish_sse_worker(queue)

        task = asyncio.create_task(cancelled_worker())
        await task
        assert queue.empty()

    asyncio.run(run())


def test_sse_sentinel_full_queue_unblocks_after_consumer_drain():
    import asyncio

    from gpt_cli.server.app import _finish_sse_worker

    async def run():
        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait("frame")

        producer = asyncio.create_task(_finish_sse_worker(queue))
        await asyncio.sleep(0)
        assert not producer.done()

        assert await queue.get() == "frame"
        await asyncio.wait_for(producer, timeout=1)
        assert await queue.get() is None

    asyncio.run(run())


def test_sse_sentinel_cancellation_propagates():
    import asyncio

    from gpt_cli.server.app import _finish_sse_worker

    async def run():
        queue = asyncio.Queue(maxsize=1)

        async def cancelled():
            task = asyncio.current_task()
            task.cancel()
            await _finish_sse_worker(queue)
            await asyncio.sleep(0)

        with pytest.raises(asyncio.CancelledError):
            await cancelled()

    asyncio.run(run())
