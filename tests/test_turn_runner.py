import asyncio
from pathlib import Path

from gpt_cli.cli.turn_runner import SessionTurnRunner
from gpt_cli.core.types import TokenBundle, TurnResult
from gpt_cli.session.live_turns import LiveTurnRegistry
from gpt_cli.session.logger import SessionLogManager
from gpt_cli.session.registry import SessionRegistry
from gpt_cli.telemetry.events import TelemetryEvent


class FakeWireTransport:
    def __init__(
        self,
        release_first: asyncio.Event,
        native_steer: bool = False,
        workspace_output: str | None = None,
        result_statuses: list[str] | None = None,
    ):
        self.release_first = release_first
        self.native_steer = native_steer
        self.workspace_output = workspace_output
        self.result_statuses = result_statuses or []
        self.initial_started = asyncio.Event()
        self.calls: list[tuple[str, str | None, str | None]] = []
        self.steer_prompts: list[str] = []
        self.latest_turn_headers = {"Authorization": "Bearer fake"}
        self.latest_turn_trace_id = "trace_1"

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def send_turn(self, prompt, conversation_id=None, parent_id=None, model_alias=None, effort=None, on_event=None):
        self.calls.append((prompt, conversation_id, parent_id))
        index = len(self.calls)
        if on_event:
            on_event(TelemetryEvent("conversation_id", {"conversation_id": "conv_1"}))
            on_event(TelemetryEvent("message_id", {"message_id": f"msg_{index}"}))
            if self.native_steer and index == 1:
                on_event(TelemetryEvent("async_task", {"async_task_id": "task_1", "async_task_turn_id": "turn_1"}))
            if self.workspace_output and index == 1:
                on_event(TelemetryEvent("tool_call_finished", {
                    "tool": "host_workspace_bind", "output": self.workspace_output,
                }))
        if index == 1:
            self.initial_started.set()
            await self.release_first.wait()
        return TurnResult(
            text=f"answer-{index}",
            conversation_id="conv_1",
            message_id=f"msg_{index}",
            model="gpt-5-6-thinking",
            status=self.result_statuses[index - 1] if index <= len(self.result_statuses) else "completed",
        )

    async def send_steer(self, prompt, context):
        self.steer_prompts.append(prompt)
        return self.native_steer


def _runner(tmp_path: Path, fake: FakeWireTransport, *, clock=None, emit_event=None) -> tuple[SessionTurnRunner, SessionRegistry]:
    sessions = tmp_path / "sessions"
    registry = SessionRegistry(sessions)
    session = registry.create_session(local_id="sess_one")
    runner = SessionTurnRunner(
        registry=registry,
        live_registry=LiveTurnRegistry(tmp_path / "runtime"),
        log_manager=SessionLogManager(sessions),
        token_bundle=TokenBundle(access_token="fake"),
        session=session,
        model="gpt-5-6-thinking",
        effort="extended",
        transport_factory=lambda _: fake,
        **({"clock": clock} if clock is not None else {}),
        **({"emit_event": emit_event} if emit_event is not None else {}),
    )
    return runner, registry


def test_runner_drains_queued_steers_in_fifo_from_post_turn_dag_pointer(tmp_path: Path):
    """Draining before a checkpoint would send queued instructions from the wrong parent."""
    async def run() -> None:
        release = asyncio.Event()
        fake = FakeWireTransport(release)
        runner, registry = _runner(tmp_path, fake)
        initial = asyncio.create_task(runner.run_initial("initial"))
        await fake.initial_started.wait()

        assert await runner.request_steer("r1", "first") == {"status": "queued", "session_id": "sess_one"}
        assert await runner.request_steer("r2", "second") == {"status": "queued", "session_id": "sess_one"}
        release.set()
        await initial

        assert fake.calls == [
            ("initial", None, None),
            ("first", "conv_1", "msg_1"),
            ("second", "conv_1", "msg_2"),
        ]
        completed = registry.require_session("sess_one")
        assert (completed.conversation_id, completed.last_message_id, completed.turns) == ("conv_1", "msg_3", 3)

    asyncio.run(run())


def test_runner_reports_native_steer_without_enqueuing_followup_turn(tmp_path: Path):
    """A native success must not duplicate the instruction as a normal queued turn."""
    async def run() -> None:
        release = asyncio.Event()
        fake = FakeWireTransport(release, native_steer=True)
        runner, _ = _runner(tmp_path, fake)
        initial = asyncio.create_task(runner.run_initial("initial"))
        await fake.initial_started.wait()

        assert await runner.request_steer("r1", "answer now") == {"status": "steered", "session_id": "sess_one"}
        release.set()
        await initial

        assert fake.steer_prompts == ["answer now"]
        assert [call[0] for call in fake.calls] == ["initial"]

    asyncio.run(run())


def test_runner_preserves_unconsumed_steers_after_incomplete_turn(tmp_path: Path):
    """Accepted steering must survive owner release when the upstream turn is unsafe to continue."""
    async def run() -> None:
        release = asyncio.Event()
        fake = FakeWireTransport(release, result_statuses=["completed", "incomplete"])
        runner, registry = _runner(tmp_path, fake)
        initial = asyncio.create_task(runner.run_initial("initial"))
        await fake.initial_started.wait()

        await runner.request_steer("r1", "first")
        await runner.request_steer("r2", "second")
        release.set()
        await initial

        assert [call[0] for call in fake.calls] == ["initial", "first"]
        session = registry.require_session("sess_one")
        assert session.pending_steers == ["first", "second"]
        assert (session.conversation_id, session.last_message_id, session.turns) == (
            "conv_1", "msg_2", 1,
        )

    asyncio.run(run())


def test_runner_drains_preserved_steers_after_explicit_resume(tmp_path: Path):
    """A safe explicit resume replays instructions saved by the previous owner."""
    async def run() -> None:
        release = asyncio.Event()
        first_fake = FakeWireTransport(release, result_statuses=["completed", "incomplete"])
        first_runner, registry = _runner(tmp_path, first_fake)
        initial = asyncio.create_task(first_runner.run_initial("initial"))
        await first_fake.initial_started.wait()
        await first_runner.request_steer("r1", "first")
        await first_runner.request_steer("r2", "second")
        release.set()
        await initial

        resumed_fake = FakeWireTransport(release)
        resumed_runner = SessionTurnRunner(
            registry=registry,
            live_registry=LiveTurnRegistry(tmp_path / "runtime"),
            log_manager=SessionLogManager(tmp_path / "sessions"),
            token_bundle=TokenBundle(access_token="fake"),
            session=registry.require_session("sess_one"),
            model="gpt-5-6-thinking",
            effort="extended",
            transport_factory=lambda _: resumed_fake,
        )
        await resumed_runner.run_initial("resume")

        assert [call[0] for call in resumed_fake.calls] == ["resume", "first", "second"]
        assert registry.require_session("sess_one").pending_steers == []

    asyncio.run(run())


def test_runner_persists_bound_bqa_workspace_from_tool_result(tmp_path: Path):
    async def run() -> None:
        release = asyncio.Event()
        release.set()
        fake = FakeWireTransport(
            release,
            workspace_output='{"chat_id":"cw-ctf-one","workspace":"/tmp/bqa/one"}',
        )
        runner, registry = _runner(tmp_path, fake)

        await runner.run_initial("solve /tmp/challenge")

        saved = registry.require_session("sess_one")
        assert saved.bqa_chat_id == "cw-ctf-one"
        assert saved.bqa_workspace_path == "/tmp/bqa/one"

    asyncio.run(run())


def test_runner_pairs_recovered_tool_result_and_records_recovery(tmp_path: Path):
    class RecoveredFake(FakeWireTransport):
        async def send_turn(self, *args, on_event=None, **kwargs):
            assert on_event is not None
            on_event(TelemetryEvent("recovery_started", {"source": "dag", "reason": "sse_interrupted"}))
            on_event(TelemetryEvent("tool_call_started", {"id": "call_1", "tool": "health_check", "payload": "{}", "source": "dag"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "call_1", "tool": "health_check", "output": '{"ok":true}', "source": "dag"}))
            return TurnResult(text="healthy", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

    async def run() -> None:
        release = asyncio.Event()
        release.set()
        runner, _ = _runner(tmp_path, RecoveredFake(release))
        await runner.run_initial("status")
        history = SessionLogManager(tmp_path / "sessions").get_history("sess_one")
        assert history[0]["recovery"] == {"source": "dag", "reason": "sse_interrupted"}
        assert history[0]["tool_calls"] == [{
            "id": "call_1", "tool": "health_check", "payload": "{}", "source": "dag",
            "result": '{"ok":true}', "result_title": None, "finished": True,
            "client_elapsed_ms": 0,
        }]

    asyncio.run(run())


def test_runner_handles_long_runtime_path_with_per_uid_fallback(tmp_path: Path):
    """SessionTurnRunner with a deeply nested long runtime directory must succeed without AF_UNIX errors."""
    async def run() -> None:
        release = asyncio.Event()
        release.set()
        fake = FakeWireTransport(release)
        sessions = tmp_path / "sessions"
        long_runtime = tmp_path / ("very_long_nested_runtime_dir_" + "w" * 90)
        assert len(str(long_runtime / "sess_one.sock").encode("utf-8")) > 103

        registry = SessionRegistry(sessions)
        session = registry.create_session(local_id="sess_one")
        live_registry = LiveTurnRegistry(long_runtime)

        runner = SessionTurnRunner(
            registry=registry,
            live_registry=live_registry,
            log_manager=SessionLogManager(sessions),
            token_bundle=TokenBundle(access_token="fake"),
            session=session,
            model="gpt-5-6-thinking",
            effort="extended",
            transport_factory=lambda _: fake,
        )

        result = await runner.run_initial("long runtime test prompt")
        assert result.status == "completed"
        assert result.text == "answer-1"
        assert fake.calls == [("long runtime test prompt", None, None)]

        # Verify live lease was released cleanly
        assert live_registry.get_live("sess_one") is None

    asyncio.run(run())


# Tool timing and cancellation regressions are intentionally isolated from transport timing.
def test_runner_persists_tool_client_elapsed_and_server_duration_separately(tmp_path: Path):
    async def run() -> None:
        release = asyncio.Event()
        release.set()
        fake = FakeWireTransport(release)

        async def send_turn_with_tool(*args, on_event=None, **kwargs):
            if on_event:
                on_event(TelemetryEvent("tool_call_started", {"id": "tool-1", "tool": "exec"}))
                on_event(TelemetryEvent("tool_call_finished", {"id": "tool-1", "tool": "terminal_execute", "output": '{"result":"ok","duration_ms":25}'}))
            return TurnResult(text="ok", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking", duration_ms=50)

        fake.send_turn = send_turn_with_tool
        clock_values = iter([1.0, 1.125])
        runner, _ = _runner(tmp_path, fake, clock=lambda: next(clock_values))
        await runner.run_initial("tool timing")

        history = SessionLogManager(tmp_path / "sessions").get_history("sess_one")
        tool = history[0]["tool_calls"][0]
        assert tool["client_elapsed_ms"] == 125
        assert tool["server_duration_ms"] == 25

    asyncio.run(run())



def test_runner_accepts_zero_ms_tool_duration_and_rejects_invalid_server_durations(tmp_path: Path):
    async def run() -> None:
        release = asyncio.Event()
        release.set()
        fake = FakeWireTransport(release)

        async def send_turn_with_durations(*args, on_event=None, **kwargs):
            on_event(TelemetryEvent("tool_call_started", {"id": "zero", "tool": "terminal_execute"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "zero", "tool": "terminal_execute", "server_duration_ms": 0, "output": "{}"}))
            on_event(TelemetryEvent("tool_call_started", {"id": "bad", "tool": "terminal_execute"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "bad", "tool": "terminal_execute", "server_duration_ms": -1, "output": "{}"}))
            on_event(TelemetryEvent("tool_call_started", {"id": "bool", "tool": "terminal_execute"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "bool", "tool": "terminal_execute", "server_duration_ms": True, "output": "{}"}))
            on_event(TelemetryEvent("tool_call_started", {"id": "text", "tool": "terminal_execute"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "text", "tool": "terminal_execute", "server_duration_ms": "nope", "output": "{}"}))
            return TurnResult(text="ok", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

        fake.send_turn = send_turn_with_durations
        clock_values = iter([1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0])
        runner, _ = _runner(tmp_path, fake, clock=lambda: next(clock_values))
        await runner.run_initial("duration validation")

        tools = {tool["id"]: tool for tool in SessionLogManager(tmp_path / "sessions").get_history("sess_one")[0]["tool_calls"]}
        assert tools["zero"]["client_elapsed_ms"] == 0
        assert tools["zero"]["server_duration_ms"] == 0
        assert "server_duration_ms" not in tools["bad"]
        assert "server_duration_ms" not in tools["bool"]
        assert "server_duration_ms" not in tools["text"]

    asyncio.run(run())

def test_runner_keeps_parallel_tool_elapsed_timings_by_id(tmp_path: Path):
    async def run() -> None:
        release = asyncio.Event()
        release.set()
        fake = FakeWireTransport(release)

        async def send_turn_with_tools(*args, on_event=None, **kwargs):
            on_event(TelemetryEvent("tool_call_started", {"id": "a", "tool": "alpha"}))
            on_event(TelemetryEvent("tool_call_started", {"id": "b", "tool": "beta"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "b", "output": "b"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "a", "output": "a"}))
            return TurnResult(text="ok", conversation_id="conv_1", message_id="msg_1", model="gpt-5-6-thinking")

        fake.send_turn = send_turn_with_tools
        clock_values = iter([1.0, 2.0, 2.4, 3.0])
        runner, _ = _runner(tmp_path, fake, clock=lambda: next(clock_values))
        await runner.run_initial("parallel tools")
        history = SessionLogManager(tmp_path / "sessions").get_history("sess_one")
        timings = {tool["id"]: tool["client_elapsed_ms"] for tool in history[0]["tool_calls"]}
        assert timings == {"b": 400, "a": 2000}

    asyncio.run(run())


def test_runner_client_cancel_preserves_completed_and_marks_only_active_tool(tmp_path: Path):
    async def run() -> None:
        release = asyncio.Event()
        fake = FakeWireTransport(release)

        async def cancelled_turn(*args, on_event=None, **kwargs):
            on_event(TelemetryEvent("tool_call_started", {"id": "done", "tool": "done_tool"}))
            on_event(TelemetryEvent("tool_call_finished", {"id": "done", "output": "done"}))
            on_event(TelemetryEvent("tool_call_started", {"id": "active", "tool": "active_tool"}))
            raise asyncio.CancelledError()

        fake.send_turn = cancelled_turn
        clock_values = iter([1.0, 1.125, 2.0, 2.25])
        runner, _ = _runner(tmp_path, fake, clock=lambda: next(clock_values))
        try:
            await runner.run_initial("cancel")
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("client cancellation must propagate")

        history = SessionLogManager(tmp_path / "sessions").get_history("sess_one")
        tools = {tool["id"]: tool for tool in history[0]["tool_calls"]}
        assert tools["done"].get("finished") is True
        assert tools["done"].get("client_elapsed_ms") == 125
        assert tools["active"]["status"] == "client_cancelled"
        assert tools["active"]["client_elapsed_ms"] == 250
        assert "backend_stopped" not in tools["active"]

    asyncio.run(run())



def test_runner_emits_calculated_and_parsed_tool_timings_to_presenter(tmp_path: Path):
    async def run() -> None:
        release = asyncio.Event()
        release.set()
        fake = FakeWireTransport(release)
        seen = []

        async def send_turn_with_tool(*args, on_event=None, **kwargs):
            on_event(TelemetryEvent("tool_call_started", {"id": "tool-1", "tool": "exec"}))
            on_event(TelemetryEvent("tool_call_finished", {
                "id": "tool-1", "tool": "terminal_execute", "output": '{"duration_ms": 25}'
            }))
            return TurnResult(text="ok", conversation_id="conv", message_id="msg", model="model")

        fake.send_turn = send_turn_with_tool
        clock_values = iter([1.0, 1.125])
        runner, _ = _runner(tmp_path, fake, clock=lambda: next(clock_values), emit_event=seen.append)
        await runner.run_initial("timing")

        finished = [e for e in seen if e.event_type == "tool_call_finished"][0]
        assert finished.data["client_elapsed_ms"] == 125
        assert finished.data["server_duration_ms"] == 25

    asyncio.run(run())
