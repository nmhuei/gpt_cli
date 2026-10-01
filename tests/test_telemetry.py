import json
import pytest
from pathlib import Path
from gpt_cli.telemetry.events import TelemetryEvent
from gpt_cli.telemetry.parser import SSETelemetryParser
from gpt_cli.telemetry.sink import TelemetrySink

def test_parse_web_search_event():
    parser = SSETelemetryParser()
    sample_frame = {
        "message": {
            "author": {"role": "tool", "name": "web.run"},
            "recipient": "all",
            "metadata": {
                "search_model_queries": {"queries": ["Linux 6.14 release date"]},
                "reasoning_status": "is_reasoning"
            }
        }
    }
    raw_sse = f"data: {json.dumps(sample_frame)}\n\n".encode("utf-8")
    events = parser.feed(raw_sse)

    types = [e.event_type for e in events]
    assert "web_search" in types
    ev = next(e for e in events if e.event_type == "web_search")
    assert ev.data["queries"] == ["Linux 6.14 release date"]

def test_parse_tool_call_frame():
    parser = SSETelemetryParser()
    sample_tool_frame = {
        "message": {
            "author": {"role": "assistant"},
            "recipient": "functions.exec",
            "channel": "commentary",
            "content": {"content_type": "text", "parts": ['{"command": "uname -r"}']},
            "status": "in_progress"
        }
    }
    raw_sse = f"data: {json.dumps(sample_tool_frame)}\n\n".encode("utf-8")
    events = parser.feed(raw_sse)

    types = [e.event_type for e in events]
    assert "tool_call_started" in types
    ev = next(e for e in events if e.event_type == "tool_call_started")
    assert ev.data["tool"] == "functions.exec"
    assert "uname -r" in str(ev.data["payload"])

def test_parse_classic_accumulated_deltas():
    parser = SSETelemetryParser()
    # First chunk: "Hello"
    f1 = {
        "conversation_id": "conv_100",
        "message": {
            "id": "msg_001",
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["Hello"]},
            "status": "in_progress",
            "channel": "final"
        }
    }
    # Second chunk: "Hello, world!"
    f2 = {
        "conversation_id": "conv_100",
        "message": {
            "id": "msg_001",
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["Hello, world!"]},
            "status": "finished_successfully",
            "channel": "final"
        }
    }
    raw = f"data: {json.dumps(f1)}\n\ndata: {json.dumps(f2)}\n\ndata: [DONE]\n\n".encode("utf-8")
    events = parser.feed(raw)

    deltas = [e.data.get("text") for e in events if e.event_type == "delta"]
    assert "".join(deltas) == "Hello, world!"

    conv_events = [e.data.get("conversation_id") for e in events if e.event_type == "conversation_id"]
    assert "conv_100" in conv_events

    msg_events = [e.data.get("message_id") for e in events if e.event_type == "message_id"]
    assert "msg_001" in msg_events

    assert any(e.event_type == "done" for e in events)


def test_parse_async_task_identity_once_from_classic_metadata():
    """Losing async-task metadata would make native steer unsafe to attempt."""
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "msg_async",
            "author": {"role": "assistant"},
            "recipient": "all",
            "channel": "commentary",
            "content": {"content_type": "text", "parts": ["working"]},
            "metadata": {"async_task_id": "task_1", "async_task_turn_id": "turn_1"},
        }
    }
    raw = f"data: {json.dumps(frame)}\n\ndata: {json.dumps(frame)}\n\n".encode("utf-8")

    events = parser.feed(raw)

    async_events = [event for event in events if event.event_type == "async_task"]
    assert [event.data for event in async_events] == [
        {"async_task_id": "task_1", "async_task_turn_id": "turn_1"}
    ]

def test_parse_v1_patch_deltas():
    parser = SSETelemetryParser()
    f1 = {"conversation_id": "conv_200", "p": "/message/content/parts/0", "o": "append", "v": "Chunk 1 "}
    f2 = {"p": "/message/content/parts/0", "o": "append", "v": "Chunk 2"}
    f3 = {"type": "message_stream_complete"}

    raw = f"data: {json.dumps(f1)}\n\ndata: {json.dumps(f2)}\n\ndata: {json.dumps(f3)}\n\n".encode("utf-8")
    events = parser.feed(raw)

    deltas = [e.data.get("text") for e in events if e.event_type == "delta"]
    assert "".join(deltas) == "Chunk 1 Chunk 2"
    assert any(e.event_type == "done" for e in events)

def test_telemetry_sink(tmp_path: Path):
    sink = TelemetrySink(log_dir=tmp_path)
    ev = TelemetryEvent(event_type="thinking", data={"text": "Deep thinking..."})
    sink.record(ev)

    log_file = tmp_path / "live_telemetry.jsonl"
    assert log_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["event_type"] == "thinking"
    assert parsed["data"]["text"] == "Deep thinking..."

def test_multiline_sse_data():
    parser = SSETelemetryParser()
    raw = (
        'data: {"message": {"id": "msg_multi", "author": {"role": "assistant"},\n'
        'data:  "content": {"content_type": "text", "parts": ["Hello multi-line"]},\n'
        'data:  "channel": "final"}}\n\n'
    )
    events = parser.feed(raw)
    deltas = [e.data.get("text") for e in events if e.event_type == "delta"]
    assert "".join(deltas) == "Hello multi-line"

def test_intermediate_tool_or_commentary_completion_does_not_emit_done():
    parser = SSETelemetryParser()
    frame1 = (
        'data: {"message": {"id": "msg_comm", "author": {"role": "assistant"}, '
        '"channel": "commentary", "content": {"content_type": "text", "parts": ["thinking..."]}, '
        '"status": "finished_successfully"}}\n\n'
    )
    frame2 = (
        'data: {"message": {"id": "msg_tool", "author": {"role": "assistant"}, '
        '"recipient": "functions.exec", "content": {"content_type": "text", "parts": ["args"]}, '
        '"status": "finished_successfully"}}\n\n'
    )
    events = parser.feed(frame1 + frame2)
    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 0, "Intermediate message statuses must not emit done"

def test_parse_reasoning_recap():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "recap_01",
            "author": {"role": "assistant"},
            "content": {"content_type": "reasoning_recap", "content": "Worked for 1m 41s"},
            "metadata": {"finished_duration_sec": 101}
        }
    }
    raw = f"data: {json.dumps(frame)}\n\n".encode("utf-8")
    events = parser.feed(raw)
    recap_events = [e for e in events if e.event_type == "reasoning_recap"]
    assert len(recap_events) == 1
    assert recap_events[0].data["recap"] == "Worked for 1m 41s"
    assert recap_events[0].data["duration_sec"] == 101

def test_parse_tool_call_finished():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "tool_resp_01",
            "author": {"role": "tool"},
            "recipient": "all",
            "content": {"content_type": "code", "parts": ["output text"]},
            "metadata": {
                "reasoning_title": "Inspecting CTF Web Challenge",
                "invoked_plugin": {"app_name": "brige"}
            }
        }
    }
    raw = f"data: {json.dumps(frame)}\n\n".encode("utf-8")
    events = parser.feed(raw)
    tf_events = [e for e in events if e.event_type == "tool_call_finished"]
    assert len(tf_events) == 1
    assert tf_events[0].data["title"] == "Inspecting CTF Web Challenge"
    assert tf_events[0].data["resource"] == {"app_name": "brige"}


def test_parse_tool_result_extracts_generic_exit_status():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "tool_timeout_result",
            "author": {"role": "tool"},
            "content": {"content_type": "text", "parts": ["command exceeded its time limit\nexit: 124"]},
            "metadata": {},
        }
    }

    events = parser.feed(f"data: {json.dumps(frame)}\n\n".encode())

    event = next(event for event in events if event.event_type == "tool_call_finished")
    assert event.data["exit_code"] == 124

def test_parse_citations():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "msg_cite",
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["Here is the info."]},
            "channel": "final",
            "metadata": {
                "citations": [
                    {"metadata": {"url": "https://example.com/ctf", "title": "CTF Writeup"}}
                ]
            }
        }
    }
    raw = f"data: {json.dumps(frame)}\n\n".encode("utf-8")
    events = parser.feed(raw)
    cites = [e for e in events if e.event_type == "citations"]
    assert len(cites) == 1
    assert cites[0].data["citations"][0]["url"] == "https://example.com/ctf"

def test_recipient_all_final_assistant_message_emits_done():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "final_01",
            "author": {"role": "assistant"},
            "recipient": "all",
            "content": {"content_type": "text", "parts": ["Done!"]},
            "status": "finished_successfully"
        }
    }
    raw = f"data: {json.dumps(frame)}\n\n".encode("utf-8")
    events = parser.feed(raw)
    done_events = [e for e in events if e.event_type == "done"]
    assert len(done_events) == 1

def test_parse_crlf_boundary_split():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "crlf_01",
            "author": {"role": "assistant"},
            "content": {"content_type": "text", "parts": ["CRLF Hello"]},
            "channel": "final"
        }
    }
    raw = f"data: {json.dumps(frame)}\r\n\r\n".encode("utf-8")
    cut = raw.index(b"\r") + 1
    events = parser.feed(raw[:cut]) + parser.feed(raw[cut:])
    deltas = [e.data.get("text") for e in events if e.event_type == "delta"]
    assert "CRLF Hello" in "".join(deltas)

def test_parse_thought_patch():
    parser = SSETelemetryParser()
    patch_frame = {
        "p": "/message/content/thoughts",
        "o": "patch",
        "v": [{"summary": "Formulating algebraic reduction algorithm"}]
    }
    raw = f"data: {json.dumps(patch_frame)}\n\n".encode("utf-8")
    events = parser.feed(raw)
    th_events = [e for e in events if e.event_type == "thinking"]
    assert len(th_events) == 1
    assert th_events[0].data["thoughts"][0]["summary"] == "Formulating algebraic reduction algorithm"

def test_tool_call_patch_does_not_leak_to_delta():
    parser = SSETelemetryParser()
    # 1. Tool start
    start_frame = {
        "message": {
            "id": "t_01",
            "author": {"role": "assistant"},
            "recipient": "host_run_command",
            "content": {"content_type": "text", "parts": []},
            "status": "in_progress"
        }
    }
    events = parser.feed(f"data: {json.dumps(start_frame)}\n\n".encode("utf-8"))
    assert any(e.event_type == "tool_call_started" for e in events)

    # 2. Tool argument append via patch
    patch_frame = {
        "p": "/message/content/parts/0",
        "o": "append",
        "v": '{"command": "uname -r"}'
    }
    events2 = parser.feed(f"data: {json.dumps(patch_frame)}\n\n".encode("utf-8"))
    deltas = [e for e in events2 if e.event_type == "delta"]
    assert len(deltas) == 0  # Must NOT leak as text delta!

def test_thoughts_node_does_not_emit_message_id():
    parser = SSETelemetryParser()
    thoughts_frame = {
        "message": {
            "id": "thought_node_999",
            "author": {"role": "assistant"},
            "content": {"content_type": "thoughts", "thoughts": [{"summary": "Thinking"}]},
            "status": "in_progress"
        }
    }
    events = parser.feed(f"data: {json.dumps(thoughts_frame)}\n\n".encode("utf-8"))
    msg_id_events = [e for e in events if e.event_type == "message_id"]
    assert len(msg_id_events) == 0, "Thinking message ID must not be emitted to prevent DAG corruption"

def test_formatter_renders_structured_payloads_without_tool_name_rules():
    from gpt_cli.telemetry.formatter import format_tool_call, format_tool_output

    title, details = format_tool_call("health_check", {"path": "/br/link_xyz/health_check"})
    assert title == "🔧 [Tool Call: health_check]"
    assert "/br/link_xyz/health_check" in details

    hc_out = format_tool_output("health_check", {
        "ok": True,
        "service": "botquanganh-host-mcp",
        "version": "1.0.0",
        "metrics": {"uptime_seconds": 120}
    })
    assert "botquanganh-host-mcp" in hc_out
    assert '"metrics"' in hc_out

    bind_out = format_tool_output("host_workspace_bind", {
        "ok": True,
        "message": "Workspace ready at /tmp/test",
        "chat_id": "cw-12345",
        "workspace_dir": "/tmp/test",
        "recent_commands": [{"cmd": "ls", "stdout": "abc"}] * 10
    })
    assert bind_out.startswith("```json")
    assert "Workspace ready" in bind_out
    assert "cw-12345" in bind_out
    wl_out = format_tool_output("workspace_list", {
        "ok": True,
        "workspaces": [{"chat_id": "sess_1"}, {"chat_id": "sess_2"}, {"chat_id": "sess_3"}, {"chat_id": "sess_4"}],
        "total_count": 4
    })
    assert wl_out.startswith("```json")
    assert '"total_count": 4' in wl_out

    big_raw = "A" * 1000
    _, call_details = format_tool_call("custom_tool", {"raw": big_raw})
    assert len(call_details) < 600
    assert "truncated" in call_details

    big_out = format_tool_output("custom_tool", big_raw)
    assert len(big_out) < 600
    assert "truncated" in big_out


def test_parser_tracks_generic_parallel_tool_ids_without_name_allowlist():
    parser = SSETelemetryParser()
    start_a = {
        "message": {
            "id": "call-a",
            "author": {"role": "assistant"},
            "recipient": "vendor.dynamic_alpha",
            "content": {"content_type": "text", "parts": ["{}"]},
            "status": "in_progress",
        }
    }
    start_b = {
        "message": {
            "id": "call-b",
            "author": {"role": "assistant"},
            "recipient": "vendor.dynamic_beta",
            "content": {"content_type": "text", "parts": ["{}"]},
            "status": "in_progress",
        }
    }
    finish_b = {
        "message": {
            "id": "result-b",
            "parent": "call-b",
            "author": {"role": "tool"},
            "content": {"content_type": "text", "parts": ["done"]},
        }
    }

    events = []
    for frame in (start_a, start_b, finish_b):
        events.extend(parser.feed(f"data: {json.dumps(frame)}\n\n".encode()))

    started = [event.data["id"] for event in events if event.event_type == "tool_call_started"]
    finished = [event.data["id"] for event in events if event.event_type == "tool_call_finished"]
    assert started == ["call-a", "call-b"]
    assert finished == ["call-b"]


def test_formatter_uses_payload_shape_not_ctf_or_workspace_tool_names():
    from gpt_cli.telemetry.formatter import format_tool_call, format_tool_output

    title, details = format_tool_call("vendor.workspace_bind", {"resume_id": "dynamic-1"})
    assert title == "🔧 [Tool Call: vendor.workspace_bind]"
    assert '"resume_id": "dynamic-1"' in details

    rendered = format_tool_output("vendor.workspace_bind", {"ok": True, "chat_id": "dynamic-1"})
    assert rendered.startswith("```json")
    assert "Workspace" not in rendered
