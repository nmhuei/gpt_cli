from gpt_cli.telemetry.bqa_activity import BQAActivityStream


def test_bqa_activity_event_is_redacted_common_host_telemetry():
    event = BQAActivityStream.event_from_record({
        "chat_id": "cw_one", "interaction_id": "op_1", "event_action": "host_run_command",
        "operation_phase": "result", "event_outcome": "success", "severity_text": "INFO",
        "command": "must never be surfaced here",
    })

    assert event.event_type == "host_activity"
    assert event.data == {
        "source": "bqa", "chat_id": "cw_one", "id": "op_1", "action": "host_run_command",
        "phase": "result", "outcome": "success", "severity": "INFO",
    }
