import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest

repo_root = Path(__file__).resolve().parent.parent

from gpt_cli.cli.main import (
    handle_run,
    handle_agent,
    handle_session,
    build_cli_parser,
)
from gpt_cli.cli.turn_runner import SessionTurnRunner
from gpt_cli.core.types import TokenBundle, TurnResult
from gpt_cli.session.live_turns import LiveTurnRegistry, SessionBusyError, TurnOwner
from gpt_cli.session.logger import SessionLogManager
from gpt_cli.session.registry import SessionRegistry, SessionLookupError
from gpt_cli.session.turn_control import TurnControlServer
from gpt_cli.telemetry.events import TelemetryEvent


class FakeTransport:
    def __init__(self, responses=None):
        self.responses = responses or []
        self.call_count = 0
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def send_turn(self, prompt, **kwargs):
        self.call_count += 1
        self.calls.append(prompt)
        if self.call_count <= len(self.responses):
            res = self.responses[self.call_count - 1]
        else:
            res = TurnResult(
                text=f"Response to {prompt}",
                conversation_id="conv_test",
                message_id=f"msg_{self.call_count}",
                model="gpt-5-6-thinking",
                status="completed",
            )
        on_event = kwargs.get("on_event")
        if on_event:
            on_event(TelemetryEvent("conversation_id", {"conversation_id": "conv_test"}))
            on_event(TelemetryEvent("message_id", {"message_id": f"msg_{self.call_count}"}))
        return res


def test_handle_run_returns_turn_result(tmp_path, monkeypatch):
    """handle_run must return TurnResult instance with status, text, etc."""
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    async def fake_send_turn(self, *args, **kwargs):
        return TurnResult(
            text="hello world",
            conversation_id="conv_1",
            message_id="msg_1",
            model="gpt-5-6-thinking",
            status="completed",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["run", "test prompt"])
    result = asyncio.run(handle_run(args))

    assert isinstance(result, TurnResult)
    assert result.text == "hello world"
    assert result.status == "completed"
    assert result.conversation_id == "conv_1"


def test_handle_agent_stops_on_failed_initial_turn(tmp_path, monkeypatch, capsys):
    """handle_agent must stop immediately when initial turn fails."""
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    call_count = 0

    async def fake_send_turn(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return TurnResult(
            text="Initial turn failed",
            conversation_id="conv_fail",
            message_id="msg_fail",
            model="gpt-5-6-thinking",
            duration_ms=0,
            status="failed",
            error="Upstream network error",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["agent", "--min-runtime-minutes", "10", "failing task"])
    asyncio.run(handle_agent(args))

    assert call_count == 1
    out = capsys.readouterr().out
    assert "Agent stopping due to turn status: failed" in out


def test_handle_agent_stops_on_failed_continuation(tmp_path, monkeypatch, capsys):
    """handle_agent must stop immediately when a continuation turn fails."""
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    call_count = 0

    async def fake_send_turn(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return TurnResult(
                text="Initial plan",
                conversation_id="conv_agent",
                message_id="msg_1",
                model="gpt-5-6-thinking",
                duration_ms=1000,
                status="completed",
            )
        return TurnResult(
            text="Continuation failed",
            conversation_id="conv_agent",
            message_id=f"msg_{call_count}",
            model="gpt-5-6-thinking",
            duration_ms=0,
            status="failed",
            error="HTTP 500",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["agent", "--min-runtime-minutes", "10", "initial task"])
    asyncio.run(handle_agent(args))

    assert call_count == 2
    out = capsys.readouterr().out
    assert "Agent stopping due to turn status: failed" in out


def test_handle_agent_json_mode_on_failed_initial_turn(tmp_path, monkeypatch, capsys):
    """handle_agent in JSON mode must output stopped status on failed initial turn."""
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    async def fake_send_turn(self, *args, **kwargs):
        return TurnResult(
            text="Fail",
            conversation_id="conv_fail",
            message_id="msg_fail",
            model="gpt-5-6-thinking",
            duration_ms=0,
            status="failed",
            error="Bad request",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["agent", "--json", "--min-runtime-minutes", "5", "task"])
    asyncio.run(handle_agent(args))

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["status"] == "stopped"
    assert payload["runtime_duration_ms"] == 0


def test_busy_session_does_not_log_turn(tmp_path, monkeypatch):
    """When SessionBusyError is raised, handle_run must not record a failed turn in history."""
    sessions_dir = tmp_path / "sessions"
    runtime_dir = tmp_path / "runtime"
    reg = SessionRegistry(sessions_dir)
    log_mgr = SessionLogManager(sessions_dir)
    sess = reg.create_session("sess_busy")
    live_reg = LiveTurnRegistry(runtime_dir)

    owner_a = TurnOwner.create_for_current_process("sess_busy", runtime_dir / "sess_busy.sock")
    live_reg.claim(owner_a)

    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: runtime_dir)

    args = build_cli_parser().parse_args(["run", "--resume-session", "sess_busy", "Prompt from Process B"])

    with pytest.raises(SessionBusyError):
        asyncio.run(handle_run(args))

    history = log_mgr.get_history("sess_busy")
    assert len(history) == 0


def test_cli_clean_errors_for_missing_sessions(tmp_path):
    """Subprocess CLI execution must return clean exit code 1 and no traceback on missing sessions."""
    import base64
    import time
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload_b64 = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode()).decode().rstrip("=")
    fake_token = f"eyJhbGciOiJIUzI1NiJ9.{payload_b64}.sig"
    (cache_dir / "webgpt-token-cache.json").write_text(json.dumps({
        "access_token": fake_token,
        "chatgpt_account_id": "acc_123"
    }))
    env = {
        **os.environ,
        "WEBGPT_CACHE_DIR": str(cache_dir),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
        "PYTHONPATH": str(repo_root),
    }

    # 1. Text mode with --resume when no active session exists
    proc = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "run", "--resume", "test prompt"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "Error:" in proc.stderr
    assert "No active local session" in proc.stderr

    # 2. Text mode with --resume-session nonexistent
    proc_named = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "run", "--resume-session", "sess_nonexistent", "test prompt"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc_named.returncode == 1
    assert "Traceback" not in proc_named.stderr
    assert "Error:" in proc_named.stderr
    assert "sess_nonexistent" in proc_named.stderr

    # 3. JSON mode with --resume
    proc_json = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "run", "--resume", "--json", "test prompt"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc_json.returncode == 1
    assert "Traceback" not in proc_json.stderr
    payload = json.loads(proc_json.stdout.strip())
    assert payload.get("status") == "error"
    assert "No active local session" in payload.get("message", "")

    # 4. JSON mode with --resume-session nonexistent
    proc_json_named = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "run", "--resume-session", "sess_nonexistent", "--json", "test prompt"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc_json_named.returncode == 1
    assert "Traceback" not in proc_json_named.stderr
    payload_named = json.loads(proc_json_named.stdout.strip())
    assert payload_named.get("status") == "error"
    assert "sess_nonexistent" in payload_named.get("message", "")

    # 5. session show nonexistent in text mode
    proc_show = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "session", "show", "sess_missing"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc_show.returncode == 1
    assert "Traceback" not in proc_show.stderr
    assert "sess_missing" in proc_show.stderr

    # 6. session show nonexistent in JSON mode
    proc_show_json = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "session", "show", "sess_missing", "--json"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc_show_json.returncode == 1
    assert "Traceback" not in proc_show_json.stderr
    payload_show = json.loads(proc_show_json.stdout.strip())
    assert payload_show.get("status") == "error"
    assert "sess_missing" in payload_show.get("message", "")

    # 7. session delete nonexistent
    proc_del = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "session", "delete", "sess_missing"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc_del.returncode == 1
    assert "Traceback" not in proc_del.stderr
    assert "sess_missing" in proc_del.stderr

    # 8. agent --resume-session nonexistent in JSON mode
    proc_agent_json = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "agent", "--resume-session", "sess_missing", "--json", "prompt"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc_agent_json.returncode == 1
    assert "Traceback" not in proc_agent_json.stderr
    payload_agent = json.loads(proc_agent_json.stdout.strip())
    assert payload_agent.get("status") == "error"
    assert "sess_missing" in payload_agent.get("message", "")


def test_run_initial_returns_final_steered_turn(tmp_path):
    """When a steer instruction runs, run_initial must return the latest queued turn result."""
    async def run():
        fake = FakeTransport([
            TurnResult(text="Initial response", conversation_id="conv_test", message_id="msg_1", model="gpt-5-6-thinking", status="completed"),
            TurnResult(text="Steered response", conversation_id="conv_test", message_id="msg_2", model="gpt-5-6-thinking", status="completed")
        ])
        reg = SessionRegistry(tmp_path / "sessions")
        sess = reg.create_session("sess_d1")
        live_reg = LiveTurnRegistry(tmp_path / "runtime")
        log_mgr = SessionLogManager(tmp_path / "sessions")

        runner = SessionTurnRunner(
            registry=reg,
            live_registry=live_reg,
            log_manager=log_mgr,
            token_bundle=TokenBundle(access_token="fake"),
            session=sess,
            model="gpt-5-6-thinking",
            effort="extended",
            transport_factory=lambda _: fake
        )

        reg.preserve_pending_steers("sess_d1", ["steer prompt"])
        result = await runner.run_initial("initial prompt")

        assert fake.calls == ["initial prompt", "steer prompt"]
        assert result.text == "Steered response"
        assert result.message_id == "msg_2"

    asyncio.run(run())


def test_session_log_respects_turn_flag_in_text_mode(tmp_path, capsys):
    """gpt session log <session_id> --turn 2 must filter to Turn #2 only in text mode."""
    log_mgr = SessionLogManager(tmp_path / "sessions")
    sess_id = "sess_d2"
    log_mgr.log_turn(sess_id, turn_index=1, prompt="Turn 1 prompt", response="Turn 1 resp", model="m", duration_ms=10)
    log_mgr.log_turn(sess_id, turn_index=2, prompt="Turn 2 prompt", response="Turn 2 resp", model="m", duration_ms=10)

    import gpt_cli.session.registry
    import gpt_cli.session.logger
    gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR = tmp_path / "sessions"
    gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR = tmp_path / "sessions"

    args = build_cli_parser().parse_args(["session", "log", sess_id, "--turn", "2"])
    handle_session(args)

    out = capsys.readouterr().out
    assert "TURN #2" in out
    assert "Turn 2 prompt" in out
    assert "TURN #1" not in out
    assert "Turn 1 prompt" not in out


def test_control_server_start_failure_releases_claim(tmp_path, monkeypatch):
    """If TurnControlServer.start() fails during SessionTurnRunner.run_initial(), claim is released."""
    async def run():
        reg = SessionRegistry(tmp_path / "sessions")
        sess = reg.create_session("sess_d6")
        live_reg = LiveTurnRegistry(tmp_path / "runtime")
        log_mgr = SessionLogManager(tmp_path / "sessions")

        async def failing_start(self):
            raise OSError("Simulated control server bind failure")

        monkeypatch.setattr(TurnControlServer, "start", failing_start)

        runner = SessionTurnRunner(
            registry=reg,
            live_registry=live_reg,
            log_manager=log_mgr,
            token_bundle=TokenBundle(access_token="fake"),
            session=sess,
            model="gpt-5-6-thinking",
            effort="extended",
            transport_factory=lambda _: FakeTransport()
        )

        with pytest.raises(OSError, match="Simulated control server bind failure"):
            await runner.run_initial("test prompt")

        assert live_reg.get_live("sess_d6") is None

    asyncio.run(run())


def test_only_workspace_bind_tools_trigger_bqa_bind(tmp_path):
    """Arbitrary tools outputting JSON with session_id must not trigger BQA workspace binding."""
    async def run():
        class BashToolTransport(FakeTransport):
            async def send_turn(self, prompt, **kwargs):
                on_event = kwargs.get("on_event")
                if on_event:
                    on_event(TelemetryEvent("tool_call_started", {"id": "c1", "tool": "bash", "payload": '{"cmd": "whoami"}'}))
                    on_event(TelemetryEvent("tool_call_finished", {"id": "c1", "tool": "bash", "output": '{"session_id": "fake_chat_99", "status": "ok"}'}))
                return TurnResult(text="done", conversation_id="conv_1", message_id="msg_1", model="m", status="completed")

        class HostBindToolTransport(FakeTransport):
            async def send_turn(self, prompt, **kwargs):
                on_event = kwargs.get("on_event")
                if on_event:
                    on_event(TelemetryEvent("tool_call_started", {"id": "c2", "tool": "host_workspace_bind", "payload": "{}"}))
                    on_event(TelemetryEvent("tool_call_finished", {"id": "c2", "tool": "host_workspace_bind", "output": '{"session_id": "real_bqa_chat_1", "workspace": "/tmp/ws"}'}))
                return TurnResult(text="done", conversation_id="conv_1", message_id="msg_2", model="m", status="completed")

        reg = SessionRegistry(tmp_path / "sessions")
        sess = reg.create_session("sess_bqa")
        runner = SessionTurnRunner(
            registry=reg,
            live_registry=LiveTurnRegistry(tmp_path / "runtime"),
            log_manager=SessionLogManager(tmp_path / "sessions"),
            token_bundle=TokenBundle(access_token="fake"),
            session=sess,
            model="m",
            effort="extended",
            transport_factory=lambda _: BashToolTransport()
        )

        await runner.run_initial("run bash")
        updated = reg.require_session("sess_bqa")
        assert updated.bqa_chat_id is None
        assert updated.bqa_workspace_path is None

        runner_bind = SessionTurnRunner(
            registry=reg,
            live_registry=LiveTurnRegistry(tmp_path / "runtime"),
            log_manager=SessionLogManager(tmp_path / "sessions"),
            token_bundle=TokenBundle(access_token="fake"),
            session=updated,
            model="m",
            effort="extended",
            transport_factory=lambda _: HostBindToolTransport()
        )
        await runner_bind.run_initial("bind workspace")
        bound = reg.require_session("sess_bqa")
        assert bound.bqa_chat_id == "real_bqa_chat_1"
        assert bound.bqa_workspace_path == "/tmp/ws"

    asyncio.run(run())


def test_main_tool_calls_contains_results_and_finished_in_json(tmp_path, monkeypatch, capsys):
    """In handle_run --json, tool_calls must include result, result_title, and finished=True."""
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    async def fake_send_turn(self, *args, on_event=None, **kwargs):
        if on_event:
            on_event(TelemetryEvent("tool_call_started", {"id": "c1", "tool": "calc", "payload": "1+1"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "c1", "tool": "calc", "output": "2", "title": "Calculated"}))
        return TurnResult(text="2", conversation_id="conv_1", message_id="msg_1", model="m", status="completed")

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args(["run", "--json", "calc"])
    asyncio.run(handle_run(args))

    out = json.loads(capsys.readouterr().out)
    tc = out["tool_calls"][0]
    assert tc.get("result") == "2"
    assert tc.get("result_title") == "Calculated"
    assert tc.get("finished") is True


def test_session_log_events_respects_tail(tmp_path, capsys):
    """gpt session log --events --tail 1 must print only the last event (+ header)."""
    log_mgr = SessionLogManager(tmp_path / "sessions")
    sess_id = "sess_d11"
    for i in range(5):
        log_mgr.log_event(sess_id, TelemetryEvent(f"event_{i}", {"seq": i}), turn_index=1)

    import gpt_cli.session.registry
    import gpt_cli.session.logger
    gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR = tmp_path / "sessions"
    gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR = tmp_path / "sessions"

    args = build_cli_parser().parse_args(["session", "log", sess_id, "--events", "--tail", "1"])
    handle_session(args)

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert "event_4" in out[1]


def test_handle_agent_with_conversation_sets_baseline_turns(tmp_path, monkeypatch):
    """handle_agent with --conversation must not fail with UnboundLocalError for baseline_turns."""
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    async def fake_get(*args, **kwargs):
        class Resp:
            status_code = 200
            def json(self):
                return {"current_node": "node_1", "title": "Conv Title"}
        return Resp()

    import curl_cffi.requests
    monkeypatch.setattr(curl_cffi.requests.AsyncSession, "get", fake_get)

    async def fake_send_turn(self, *args, **kwargs):
        return TurnResult(
            text="Initial agent output",
            conversation_id="conv_attach_123",
            message_id="msg_1",
            model="gpt-5-6-thinking",
            duration_ms=50,
            status="completed",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args([
        "agent", "--conversation", "conv_attach_123", "--min-runtime-minutes", "0.0001", "task"
    ])
    asyncio.run(handle_agent(args))
    # Must complete without UnboundLocalError


def test_resumed_agent_does_not_count_old_incomplete_log_toward_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr("gpt_cli.cli.main.TokenCacheManager.load_disk_cache", lambda self: TokenBundle(access_token="fake"))
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: tmp_path / "runtime")

    registry = SessionRegistry()
    session = registry.create_session(local_id="sess_old_incomplete")
    registry.checkpoint(session.local_id, "conv_existing", "msg_existing", "gpt-5-6-thinking")
    SessionLogManager().log_turn(
        local_id=session.local_id,
        turn_index=1,
        prompt="previous attempt",
        response="partial",
        model="gpt-5-6-thinking",
        duration_ms=1000,
        error="SSE interrupted",
    )

    call_count = 0

    async def fake_send_turn(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return TurnResult(
            text="completed",
            conversation_id="conv_existing",
            message_id=f"msg_{call_count}",
            model="gpt-5-6-thinking",
            duration_ms=100,
            status="completed",
        )

    monkeypatch.setattr("gpt_cli.cli.main.WireTransport.send_turn", fake_send_turn)
    args = build_cli_parser().parse_args([
        "agent", "--resume-session", session.local_id,
        "--min-runtime-minutes", "0.003", "continue work",
    ])

    asyncio.run(handle_agent(args))

    assert call_count == 2


def test_normalize_tool_payload_preserves_multiline_bash_and_grep():
    """_normalize_tool_payload must not wrap multiline bash or grep commands in python3 -c."""
    from gpt_cli.server.tool_utils import _normalize_tool_payload

    # 1. Multi-line bash loop
    loop_cmd = "for i in 1 2 3; do\n  echo $i\ndone"
    p1 = _normalize_tool_payload("bash", json.dumps({"input": loop_cmd}))
    assert p1["command"] == loop_cmd
    assert not p1["command"].startswith("python3 -c")

    # 2. Command containing "import "
    grep_cmd = 'git grep "import os"'
    p2 = _normalize_tool_payload("bash", json.dumps({"cmd": grep_cmd}))
    assert p2["command"] == grep_cmd
    assert not p2["command"].startswith("python3 -c")

    # 3. Explicit Python code in code field is wrapped in python3 -c
    py_code = "import sys\nprint(sys.version)"
    p3 = _normalize_tool_payload("bash", json.dumps({"code": py_code}))
    assert p3["command"].startswith("python3 -c")
    assert "import sys" in p3["command"]


def test_resolve_tool_name_empty_candidate():
    """_resolve_tool_name must not match arbitrary tools when candidate normalizes to empty string."""
    from gpt_cli.server.tool_utils import _resolve_tool_name

    res, unwrap = _resolve_tool_name("---", {"bash", "read_file"})
    assert res == "---"
    assert unwrap is False


def test_chat_completions_non_streaming_worker_error_returns_json_500(monkeypatch):
    """chat_completions non-streaming must return JSON 500 when WireTransport raises."""
    from starlette.testclient import TestClient
    from gpt_cli.server.app import create_app
    from gpt_cli.core.types import TokenBundle

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )

    async def failing_send_turn(self, *args, **kwargs):
        raise RuntimeError("Conduit network failure")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", failing_send_turn)

    client = TestClient(create_app())
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-5-6-thinking",
            "stream": False,
            "messages": [{"role": "user", "content": "test error handling"}]
        }
    )
    assert res.status_code == 500
    payload = res.json()
    assert "error" in payload
    assert "Conduit network failure" in payload["error"]["message"]
    assert payload["error"]["type"] == "api_error"


def test_anthropic_messages_non_streaming_worker_error_returns_json_500(monkeypatch):
    """anthropic_messages non-streaming must return JSON 500 when WireTransport raises."""
    from starlette.testclient import TestClient
    from gpt_cli.server.app import create_app
    from gpt_cli.core.types import TokenBundle

    monkeypatch.setattr(
        "gpt_cli.server.app.TokenCacheManager.load_disk_cache",
        lambda self: TokenBundle(access_token="fake_token")
    )

    async def failing_send_turn(self, *args, **kwargs):
        raise RuntimeError("Anthropic upstream reset")

    monkeypatch.setattr("gpt_cli.server.app.WireTransport.send_turn", failing_send_turn)

    client = TestClient(create_app())
    res = client.post(
        "/v1/messages",
        json={
            "model": "gpt-5-6-thinking",
            "stream": False,
            "messages": [{"role": "user", "content": "test error handling"}]
        }
    )
    assert res.status_code == 500
    payload = res.json()
    assert payload.get("type") == "error"
    assert "Anthropic upstream reset" in payload["error"]["message"]
    assert payload["error"]["type"] == "api_error"


def test_conduit_prepare_turn_preserves_http_status_on_sentinel_error():
    """ConduitHandshake.prepare_turn must raise RuntimeError with HTTP status on Sentinel error."""
    from unittest.mock import AsyncMock, patch
    from gpt_cli.transport.conduit import ConduitHandshake

    async def run():
        class MockResp:
            status_code = 401
            text = "Unauthorized"
            def json(self):
                return {}

        session = AsyncMock()
        session.post = AsyncMock(return_value=MockResp())
        bundle = TokenBundle(access_token="fake_token", oai_device_id="dev")

        with patch("gpt_cli.transport.conduit.bootstrap_proof_token", return_value="boot"):
            with pytest.raises(RuntimeError, match="HTTP 401"):
                await ConduitHandshake.prepare_turn(session, bundle, "gpt-5-6-thinking")

    asyncio.run(run())


def test_token_cache_ignores_user_id_as_account_id(tmp_path):
    """TokenCacheManager must never treat user- prefixed IDs as account IDs."""
    from gpt_cli.core.token_cache import TokenCacheManager

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "webgpt-token-cache.json"
    cache_file.write_text(json.dumps({
        "access_token": "fake_token",
        "accountId": "user-gWIfeiBlko2qiTF1PA7DLnjJ",
    }))

    mgr = TokenCacheManager(cache_dir)
    bundle = mgr.load_disk_cache()
    assert bundle is not None
    assert bundle.chatgpt_account_id is None


def test_cli_handles_corrupt_session_registry_cleanly(tmp_path):
    """Subprocess CLI execution must exit with clean error code 1 when session registry is corrupt."""
    import base64
    import time
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload_b64 = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode()).decode().rstrip("=")
    fake_token = f"eyJhbGciOiJIUzI1NiJ9.{payload_b64}.sig"
    (cache_dir / "webgpt-token-cache.json").write_text(json.dumps({
        "access_token": fake_token,
    }))

    sessions_dir = tmp_path / "data" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    (sessions_dir / "tool_sessions.json").write_text("{ corrupt json invalid")

    env = {
        **os.environ,
        "WEBGPT_CACHE_DIR": str(cache_dir),
        "WEBGPT_SESSION_DIR": str(sessions_dir),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_RUNTIME_DIR": str(tmp_path / "runtime"),
        "PYTHONPATH": str(repo_root),
    }

    proc = subprocess.run(
        [sys.executable, "-m", "gpt_cli.cli.main", "session", "list"],
        capture_output=True, text=True, cwd=str(tmp_path), env=env,
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "Corrupt session registry" in proc.stderr



def test_main_handle_agent_remains_compatibility_wrapper(monkeypatch):
    """main.handle_agent must continue exporting the public compatibility entrypoint."""
    import gpt_cli.cli.agent_runner as agent_runner
    import gpt_cli.cli.main as main

    seen = {}

    async def fake_runner(args, *, handle_run):
        seen["args"] = args
        seen["handle_run"] = handle_run

    monkeypatch.setattr(agent_runner, "handle_agent", fake_runner)

    async def fake_handle_run(_args):
        pass

    args = argparse.Namespace()
    asyncio.run(main.handle_agent(args))

    assert seen["args"] is args
    assert seen["handle_run"] is main.handle_run


def test_cli_delete_session_preserves_other_data_and_rejects_live_race(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: runtime_dir)

    reg = SessionRegistry(sessions_dir)
    reg.create_session(local_id="sess_delete")
    reg.create_session(local_id="sess_keep")
    logs = SessionLogManager(sessions_dir)
    logs.log_turn("sess_delete", 1, "delete", "delete", "m", 1)
    logs.log_turn("sess_keep", 1, "keep", "keep", "m", 1)

    owner = TurnOwner.create_for_current_process("sess_delete", runtime_dir / "delete.sock")
    LiveTurnRegistry(runtime_dir).claim(owner)

    args = build_cli_parser().parse_args(["session", "delete", "sess_delete"])
    with pytest.raises(SessionBusyError):
        handle_session(args)

    assert reg.get_session("sess_delete") is not None
    assert logs.get_history("sess_keep")
    assert logs.get_history("sess_delete")


def test_delete_validates_path_before_registry_mutation(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    reg = SessionRegistry(sessions_dir)
    reg.create_session(local_id=".")
    args = build_cli_parser().parse_args(["session", "delete", "."])
    with pytest.raises(ValueError):
        handle_session(args)
    assert reg.get_session(".") is not None





def test_turn_runner_aborts_after_claim_if_session_deleted_interleaving(tmp_path):
    """A session removed between CLI resolution and live claim must not reach transport."""
    import asyncio

    from gpt_cli.cli.turn_runner import SessionTurnRunner
    from gpt_cli.session.registry import SessionRegistry, SessionLookupError
    from gpt_cli.session.logger import SessionLogManager
    from gpt_cli.session.live_turns import LiveTurnRegistry
    from gpt_cli.core.types import TokenBundle

    class ExplodingTransport:
        def __init__(self, *_args):
            raise AssertionError("transport must not be created after deleted-session claim")

    reg = SessionRegistry(tmp_path / "sessions")
    sess = reg.create_session(local_id="sess_interleave")

    class DeleteAfterClaim(LiveTurnRegistry):
        def claim(self, owner):
            result = super().claim(owner)
            reg.delete_session(sess.local_id)
            return result

    runner = SessionTurnRunner(
        registry=reg,
        live_registry=DeleteAfterClaim(tmp_path / "runtime"),
        log_manager=SessionLogManager(tmp_path / "logs"),
        token_bundle=TokenBundle(access_token="fake"),
        session=sess,
 model="test", 
        effort="extended",
        transport_factory=ExplodingTransport,
    )

    with pytest.raises(SessionLookupError, match="sess_interleave"):
        asyncio.run(runner.run_initial("hello"))

    assert runner.live_registry.get_live("sess_interleave") is None


def test_cli_delete_session_end_to_end_removes_target_and_preserves_other_session(tmp_path, monkeypatch):
    """CLI delete removes only the requested session metadata and logs."""
    sessions_dir = tmp_path / "sessions"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: runtime_dir)

    reg = SessionRegistry(sessions_dir)
    reg.create_session(local_id="sess_delete")
    reg.create_session(local_id="sess_keep")
    logs = SessionLogManager(sessions_dir)
    logs.log_turn("sess_delete", 1, "delete", "deleted", "m", 1)
    logs.log_turn("sess_keep", 1, "keep", "kept", "m", 1)

    handle_session(build_cli_parser().parse_args(["session", "delete", "sess_delete"]))

    assert reg.get_session("sess_delete") is None
    assert logs.get_history("sess_delete") == []
    assert reg.get_session("sess_keep") is not None
    assert logs.get_history("sess_keep")


def test_cli_delete_log_cleanup_failure_does_not_report_success(tmp_path, monkeypatch, capsys):
    """A failed local log cleanup must not print a successful delete message."""
    sessions_dir = tmp_path / "sessions"
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", sessions_dir)

    reg = SessionRegistry(sessions_dir)
    reg.create_session(local_id="sess_delete")

    def fail_cleanup(_self, _local_id):
        raise OSError("log cleanup failed")

    monkeypatch.setattr(SessionLogManager, "delete_session_logs", fail_cleanup)

    with pytest.raises(Exception, match="Failed to remove local logs"):
        handle_session(build_cli_parser().parse_args(["session", "delete", "sess_delete"]))

    captured = capsys.readouterr()
    assert "Deleted session" not in captured.out


def test_cli_delete_cleanup_failure_keeps_metadata_and_returns_error(tmp_path, monkeypatch, capsys):
    """Log cleanup happens before metadata removal, so failures remain retryable."""
    sessions_dir = tmp_path / "sessions"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setattr("gpt_cli.session.registry.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.session.logger.defaults.DEFAULT_SESSION_DIR", sessions_dir)
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: runtime_dir)

    reg = SessionRegistry(sessions_dir)
    reg.create_session(local_id="sess_retry")

    def fail_cleanup(_local_id):
        raise OSError("disk unavailable")

    monkeypatch.setattr(SessionLogManager, "delete_session_logs", fail_cleanup)

    args = build_cli_parser().parse_args(["session", "delete", "sess_retry"])
    with pytest.raises(Exception, match="Failed to remove local logs"):
        handle_session(args)

    assert reg.get_session("sess_retry") is not None


def test_main_delete_cleanup_failure_text_and_json_exit_cleanly(tmp_path):
    """main() handles cleanup errors without traceback or false success."""
    import subprocess

    code = r"""
import os
import sys
from pathlib import Path
from gpt_cli.cli import main as cli_main
from gpt_cli.session.logger import SessionLogManager
from gpt_cli.session.registry import SessionRegistry
root = Path(os.environ['WEBGPT_SESSION_DIR'])
SessionRegistry(root).create_session(local_id='sess_fail')
SessionLogManager.delete_session_logs = lambda self, _: (_ for _ in ()).throw(OSError('disk unavailable'))
from gpt_cli.config import defaults
defaults.user_runtime_dir = lambda: Path(os.environ['XDG_RUNTIME_DIR'])
sys.argv = ['gpt', 'session', 'delete', 'sess_fail'] + (['--json'] if os.environ.get('JSON_MODE') else [])
cli_main.main()
"""
    for json_mode in (False, True):
        env = dict(os.environ)
        env['WEBGPT_SESSION_DIR'] = str(tmp_path / ('json' if json_mode else 'text'))
        if json_mode:
            env['JSON_MODE'] = '1'
        env['XDG_RUNTIME_DIR'] = str(tmp_path / 'runtime' / ('json' if json_mode else 'text'))
        proc = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
        combined = proc.stdout + proc.stderr
        assert proc.returncode == 1
        assert 'Traceback' not in combined
        assert 'Deleted session' not in combined
        assert 'Failed to remove local logs' in combined or 'disk unavailable' in combined

def test_main_delete_invalid_path_error_is_clean(tmp_path):
    import subprocess

    code = """
import os
import sys
from pathlib import Path
from gpt_cli.cli import main as cli_main
from gpt_cli.config import defaults
defaults.user_runtime_dir = lambda: Path(os.environ['XDG_RUNTIME_DIR'])
sys.argv = ['gpt', 'session', 'delete', '.']
cli_main.main()
"""
    env = {**os.environ, 'WEBGPT_SESSION_DIR': str(tmp_path / 'sessions'), 'XDG_RUNTIME_DIR': str(tmp_path / 'runtime')}
    proc = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, env=env)
    assert proc.returncode == 1
    assert 'Traceback' not in (proc.stdout + proc.stderr)
    assert 'Error:' in proc.stderr


def test_main_help_word_prints_help_without_starting_a_turn(monkeypatch, capsys):
    """The standalone help word must never become an upstream prompt."""
    import gpt_cli.cli.main as cli_main

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("help must not start a model turn")

    monkeypatch.setattr(cli_main, "handle_run", fail_if_called)
    monkeypatch.setattr(sys, "argv", ["gpt", "help"])

    with pytest.raises(SystemExit) as exc_info:
        cli_main.main()

    assert exc_info.value.code == 0
    assert "usage: gpt" in capsys.readouterr().out


def test_main_plugin_help_word_prints_plugin_help(monkeypatch, capsys):
    """The plugin help word must map to argparse help."""
    import gpt_cli.cli.main as cli_main

    monkeypatch.setattr(sys, "argv", ["gpt", "plugin", "help"])

    with pytest.raises(SystemExit) as exc_info:
        cli_main.main()

    assert exc_info.value.code == 0
    assert "usage: gpt plugin" in capsys.readouterr().out


def test_main_reports_unexpected_operational_errors_without_traceback(monkeypatch, capsys):
    """Unhandled command errors must have one clean CLI error boundary."""
    import gpt_cli.cli.main as cli_main

    monkeypatch.setattr(sys, "argv", ["gpt", "status"])
    monkeypatch.setattr(
        cli_main,
        "status_daemon",
        lambda: (_ for _ in ()).throw(RuntimeError("daemon socket unavailable")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_main.main()

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "Error: daemon socket unavailable" in stderr
    assert "Traceback" not in stderr


def test_main_handles_keyboard_interrupt_without_traceback(tmp_path):
    """Ctrl+C during a turn must exit with the shell-standard interrupt code."""
    code = """
import sys
from gpt_cli.cli import main as cli_main

async def interrupt_turn(*_args, **_kwargs):
    raise KeyboardInterrupt

cli_main.handle_run = interrupt_turn
sys.argv = ['gpt', 'run', 'test prompt']
cli_main.main()
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env={**os.environ, "PYTHONPATH": str(repo_root)},
    )

    assert proc.returncode == 130
    assert "Cancelled by user." in proc.stderr
    assert "Traceback" not in proc.stderr



def test_main_delete_registry_save_failure_reports_partial_delete_and_keeps_registry(tmp_path, monkeypatch, capsys):
    """A registry persistence failure after log removal is a clean partial delete."""
    import sys
    import gpt_cli.cli.main as cli_main

    sessions_dir = tmp_path / "sessions"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("WEBGPT_SESSION_DIR", str(sessions_dir))
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: runtime_dir)

    reg = SessionRegistry(sessions_dir)
    reg.create_session(local_id="sess_partial")
    logs = SessionLogManager(sessions_dir)
    logs.log_turn("sess_partial", 1, "user", "answer", "m", 1)

    monkeypatch.setattr(SessionRegistry, "_save", lambda self: (_ for _ in ()).throw(OSError("registry disk failure")))
    sys.argv = ["gpt", "session", "delete", "sess_partial"]

    with pytest.raises(SystemExit) as exc:
        cli_main.main()
    assert exc.value.code == 1

    combined = capsys.readouterr().err
    assert "Traceback" not in combined
    assert "Logs removed but failed to update registry" in combined

    # New registry reader sees the original on-disk entry because replace never succeeded.
    monkeypatch.undo()
    reg2 = SessionRegistry(sessions_dir)
    assert reg2.get_session("sess_partial") is not None
    assert not SessionLogManager(sessions_dir).get_history("sess_partial")



def test_cli_delete_corrupt_live_registry_is_clean_and_preserves_bytes(tmp_path, monkeypatch, capsys):
    """A corrupt live-turn registry must block CLI deletion without traceback."""
    import gpt_cli.cli.main as cli_main

    sessions_dir = tmp_path / "sessions"
    runtime_dir = tmp_path / "runtime"
    monkeypatch.setenv("WEBGPT_SESSION_DIR", str(sessions_dir))
    monkeypatch.setattr("gpt_cli.cli.main.defaults.user_runtime_dir", lambda: runtime_dir)

    SessionRegistry(sessions_dir).create_session(local_id="sess_corrupt_live")
    live_file = runtime_dir / "live_turns.json"
    live_file.parent.mkdir(parents=True, exist_ok=True)
    corrupt = "{broken-live-state"
    live_file.write_text(corrupt, encoding="utf-8")

    sys.argv = ["gpt", "session", "delete", "sess_corrupt_live"]
    with pytest.raises(SystemExit) as exc:
        cli_main.main()

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert "Traceback" not in stderr
    assert "Corrupt live-turn registry" in stderr
    assert live_file.read_text(encoding="utf-8") == corrupt
    assert SessionRegistry(sessions_dir).get_session("sess_corrupt_live") is not None
