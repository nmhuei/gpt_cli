from io import StringIO

from gpt_cli.telemetry.events import TelemetryEvent
from gpt_cli.telemetry.presenter import TerminalTelemetryPresenter


def test_presenter_renders_recovery_as_compact_progress_without_raw_dump():
    output = StringIO()
    presenter = TerminalTelemetryPresenter(output, color=False)

    presenter.emit(TelemetryEvent("recovery_started", {"source": "dag", "reason": "sse_interrupted"}))
    presenter.emit(TelemetryEvent("tool_call_started", {
        "id": "call_1", "tool": "health_check", "payload": '{"path":"/br/health_check"}', "source": "dag",
    }))
    presenter.emit(TelemetryEvent("tool_call_finished", {
        "id": "call_1", "tool": "health_check", "output": {"ok": True}, "source": "dag",
    }))
    presenter.emit(TelemetryEvent("tool_call_finished", {
        "id": "call_1", "tool": "health_check", "output": {"ok": True}, "source": "dag",
    }))

    rendered = output.getvalue()
    assert "Recovering completed server activity" in rendered
    assert "health_check: /br/health_check (recovered)" in rendered
    assert "✓ health_check: ok (recovered)" in rendered
    assert rendered.count("✓ health_check") == 1


def test_presenter_renders_tool_durations_without_payload_or_result():
    output = StringIO()
    presenter = TerminalTelemetryPresenter(output, color=False)

    presenter.emit(TelemetryEvent("tool_call_finished", {
        "id": "call_1",
        "tool": "terminal_execute",
        "payload": "secret command",
        "output": {"secret": "result"},
        "client_elapsed_ms": 0,
        "server_duration_ms": 25,
    }))

    rendered = output.getvalue()
    assert "client 0 ms" in rendered
    assert "server 25 ms" in rendered
    assert "secret command" not in rendered
    assert "secret" in rendered



def test_presenter_uses_event_timings_and_keeps_result_summary_context():
    output = StringIO()
    presenter = TerminalTelemetryPresenter(output, color=False)

    event = TelemetryEvent("tool_call_finished", {
        "id": "call_2",
        "tool": "terminal_execute",
        "output": {"ok": True},
    })
    event.data["client_elapsed_ms"] = 7
    event.data["server_duration_ms"] = 0
    presenter.emit(event)

    rendered = output.getvalue()
    assert "ok" in rendered
    assert "client 7 ms" in rendered
    assert "server 0 ms" in rendered



def test_presenter_rejects_invalid_duration_values():
    output = StringIO()
    presenter = TerminalTelemetryPresenter(output, color=False)

    presenter.emit(TelemetryEvent("tool_call_finished", {
        "id": "call_bad",
        "tool": "terminal_execute",
        "output": "done",
        "client_elapsed_ms": True,
        "server_duration_ms": -1,
    }))
    rendered = output.getvalue()
    assert "client" not in rendered
    assert "server" not in rendered
