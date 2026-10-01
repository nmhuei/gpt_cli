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
