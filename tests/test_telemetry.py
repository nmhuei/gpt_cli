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
