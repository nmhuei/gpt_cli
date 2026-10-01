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


def _runner(tmp_path: Path, fake: FakeWireTransport) -> tuple[SessionTurnRunner, SessionRegistry]:
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
        assert registry.require_session("sess_one").pending_steers == ["first", "second"]

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
