import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from gpt_cli.core.types import SteerContext, TokenBundle, TurnResult
from gpt_cli.models.registry import ModelRegistry
from gpt_cli.transport.wire import WireTransport
from gpt_cli.transport.conduit import ConduitHandshake
from gpt_cli.transport.conversation import ConversationRecovery, ConversationState
from gpt_cli.transport.lifecycle import LifecycleStatus, classify_lifecycle
from gpt_cli.telemetry.events import TelemetryEvent

def test_model_registry_resolution():
    model = ModelRegistry.resolve("5.6")
    assert model.slug == "gpt-5-6-thinking"
    assert model.default_effort == "extended"

    sol_model = ModelRegistry.resolve("sol")
    assert sol_model.slug == "gpt-5-6-thinking"


def test_tool_timeout_is_classified_from_structured_result():
    event = TelemetryEvent(event_type="tool_call_finished", data={"exit_code": 124})
    assert classify_lifecycle([event], None) is LifecycleStatus.TOOL_TIMEOUT


def test_upstream_error_is_not_misreported_as_model_terminal():
    event = TelemetryEvent(event_type="error", data={"error": "upstream failed"})
    assert classify_lifecycle([event], None) is LifecycleStatus.UPSTREAM_ERROR


def test_current_upstream_tool_node_is_server_active():
    state = ConversationState(
        role="assistant",
        recipient="api_tool.call_tool",
        content_type="code",
    )
    assert classify_lifecycle([], state) is LifecycleStatus.SERVER_ACTIVE


def test_final_upstream_text_is_model_terminal():
    state = ConversationState(
        role="assistant",
        recipient="all",
        content_type="text",
        finish_type="stop",
    )
    assert classify_lifecycle([], state) is LifecycleStatus.MODEL_TERMINAL


def test_conversation_recovery_fetches_current_node_with_target_route():
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "current_node": "node_final",
            "mapping": {
                "node_final": {
                    "message": {
                        "author": {"role": "assistant"},
                        "recipient": "all",
                        "content": {"content_type": "text"},
                        "status": "finished_successfully",
                        "metadata": {"finish_details": {"type": "stop"}},
                    }
                }
            },
        }
        session.get = AsyncMock(return_value=response)
        recovery = ConversationRecovery(bundle, session=session)

        state = await recovery.fetch("conv_123")

        assert state == ConversationState(
            role="assistant",
            recipient="all",
            content_type="text",
            message_status="finished_successfully",
            finish_type="stop",
        )
        _, kwargs = session.get.call_args
        assert kwargs["headers"]["x-openai-target-path"] == "/backend-api/conversation/conv_123"
        assert kwargs["headers"]["x-openai-target-route"] == "/backend-api/conversation/conv_123"
        assert "Content-Type" not in kwargs["headers"]

    asyncio.run(_run())

def test_wire_payload_construction():
    bundle = TokenBundle(access_token="fake", oai_device_id="dev")
    transport = WireTransport(bundle)

    payload = transport.build_payload(
        prompt="Test message",
        conversation_id="conv_123",
        parent_id="msg_456",
        model_alias="5.6"
    )

    assert payload["model"] == "gpt-5-6-thinking"
    assert payload["thinking_effort"] == "extended"
    assert payload["conversation_id"] == "conv_123"
    assert payload["parent_message_id"] == "msg_456"
    assert payload["messages"][0]["content"]["parts"] == ["Test message"]


def test_wire_payload_carries_selected_mcp_connector_as_system_hint():
    transport = WireTransport(TokenBundle(access_token="fake", oai_device_id="dev"))

    payload = transport.build_payload(
        prompt="@br bind Chrono_II; solve",
        system_hints=["connector_br"],
    )

    assert payload["system_hints"] == ["connector_br"]


def test_wire_payload_carries_selected_mcp_source_in_user_metadata():
    transport = WireTransport(TokenBundle(access_token="fake", oai_device_id="dev"))
    source = {"id": "connector_br", "name": "br", "actions": []}

    payload = transport.build_payload(
        prompt="@br bind Chrono_II; solve",
        selected_mcp_sources=[source],
    )

    assert payload["messages"][0]["metadata"]["selected_mcp_sources"] == [source]


def test_wire_turn_passes_connector_hint_to_prepare_and_conversation():
    async def _run():
        transport = WireTransport(TokenBundle(access_token="fake", oai_device_id="dev"))

        class Response:
            status_code = 200

            async def aiter_content(self):
                yield b'data: {"conversation_id":"conv_1","message":{"id":"msg_1","author":{"role":"assistant"},"recipient":"all","channel":"final","content":{"content_type":"text","parts":["done"]},"status":"finished_successfully"}}\n\n'

        with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as prepare:
            prepare.return_value = ({"Authorization": "Bearer fake"}, "wire_session", "trace_1", None)
            transport.session.post = AsyncMock(return_value=Response())

            await transport._send_once("@br bind Chrono_II; solve", system_hints=["connector_br"])

        assert prepare.call_args.kwargs["system_hints"] == ["connector_br"]
        assert transport.session.post.call_args.kwargs["json"]["system_hints"] == ["connector_br"]

    asyncio.run(_run())


def test_wire_steer_payload_uses_observed_async_turn_context():
    """A wrong route or missing async id would steer an unrelated upstream turn."""
    async def _run():
        transport = WireTransport(TokenBundle(access_token="fake", oai_device_id="dev"))
        context = SteerContext(
            conversation_id="conv_1",
            parent_message_id="msg_1",
            model="gpt-5-6-thinking",
            async_task_id="task_1",
            async_task_turn_id="turn_1",
            headers={"x-oai-turn-trace-id": "trace_1", "Accept": "text/event-stream"},
        )
        response = MagicMock(status_code=200)
        transport.session.post = AsyncMock(return_value=response)

        accepted = await transport.send_steer("answer with the risks", context)

        assert accepted is True
        request_url, = transport.session.post.call_args.args
        request = transport.session.post.call_args.kwargs
        assert request_url == "https://chatgpt.com/backend-api/f/steer_turn"
        assert request["headers"]["x-openai-target-path"] == "/backend-api/f/steer_turn"
        assert request["headers"]["x-openai-target-route"] == "/backend-api/f/steer_turn"
        assert request["headers"]["Accept"] == "application/json"
        assert request["json"]["async_task_id"] == "task_1"
        assert request["json"]["async_task_turn_id"] == "turn_1"
        assert request["json"]["conversation_id"] == "conv_1"
        assert request["json"]["parent_message_id"] == "msg_1"
        assert request["json"]["messages"][0]["content"]["parts"] == ["answer with the risks"]

    asyncio.run(_run())


def test_wire_steer_rejection_is_not_reported_as_success():
    """Treating a non-2xx steer response as success would silently drop an instruction."""
    async def _run():
        transport = WireTransport(TokenBundle(access_token="fake", oai_device_id="dev"))
        context = SteerContext(
            conversation_id="conv_1",
            parent_message_id="msg_1",
            model="gpt-5-6-thinking",
            async_task_id="task_1",
            headers={},
        )
        transport.session.post = AsyncMock(return_value=MagicMock(status_code=409))

        assert await transport.send_steer("queue me", context) is False

    asyncio.run(_run())


def test_wire_retains_prepared_headers_for_a_live_native_steer():
    """Discarding prepared auth headers would force every native steer to fail."""
    async def _run():
        transport = WireTransport(TokenBundle(access_token="fake", oai_device_id="dev"))

        class Response:
            status_code = 200

            async def aiter_content(self):
                yield b'data: {"conversation_id":"conv_1","message":{"id":"msg_1","author":{"role":"assistant"},"recipient":"all","channel":"final","content":{"content_type":"text","parts":["done"]},"status":"finished_successfully"}}\n\n'

        with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as prepare:
            prepare.return_value = ({"Authorization": "Bearer fake"}, "wire_session", "trace_1", None)
            transport.session.post = AsyncMock(return_value=Response())
            await transport._send_once("hi")

        assert transport.latest_turn_headers == {"Authorization": "Bearer fake"}
        assert transport.latest_turn_trace_id == "trace_1"

    asyncio.run(_run())

def test_wire_incomplete_stream_marked_properly():
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        transport = WireTransport(bundle)

        class MockResponse:
            status_code = 200
            text = "OK"

            async def aiter_content(self):
                chunk = b'data: {"message": {"id": "m1", "author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["Partial response..."]}, "channel": "final"}}\n\n'
                yield chunk
                raise ConnectionResetError("Connection lost mid-stream")

        with patch.object(transport.session, "post", new_callable=AsyncMock) as mock_post:
            with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as mock_prep:
                mock_prep.return_value = ({}, "s_id", "t_id", None)
                mock_post.return_value = MockResponse()

                result = await transport._send_once(prompt="hi")
                assert result.status == "incomplete"
                assert result.text == "Partial response..."
                assert "Connection lost" in str(result.error)

    asyncio.run(_run())


def test_wire_reports_server_active_when_sse_drops_during_upstream_tool():
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        transport = WireTransport(bundle)

        class MockResponse:
            status_code = 200

            async def aiter_content(self):
                yield b'data: {"conversation_id":"conv_live","message":{"id":"tool_live","author":{"role":"assistant"},"recipient":"vendor.dynamic","content":{"content_type":"text","parts":["{}"]}}}\n\n'
                raise ConnectionResetError("socket dropped")

        with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as mock_prep:
            mock_prep.return_value = ({}, "s1", "t1", None)
            with patch.object(transport.session, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = MockResponse()
                with patch(
                    "gpt_cli.transport.conversation.ConversationRecovery.fetch",
                    new_callable=AsyncMock,
                    return_value=ConversationState(
                        role="assistant", recipient="vendor.dynamic", content_type="text"
                    ),
                ):
                    result = await transport._send_once(prompt="solve /tmp/challenge")

        assert result.status == "incomplete"
        assert result.lifecycle_status == "server_active"
        assert "socket dropped" in str(result.error)

    asyncio.run(_run())

def test_wire_http_error_text_property_extraction():
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        transport = WireTransport(bundle)

        class MockResponseError:
            status_code = 404
            text = '{"detail": "Resource not found"}'

        with patch.object(transport.session, "post", new_callable=AsyncMock) as mock_post:
            with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as mock_prep:
                mock_prep.return_value = ({}, "s_id", "t_id", None)
                mock_post.return_value = MockResponseError()

                with pytest.raises(RuntimeError) as exc_info:
                    await transport._send_once(prompt="hi")
                assert "HTTP 404" in str(exc_info.value)
                assert "Resource not found" in str(exc_info.value)

    asyncio.run(_run())

def test_wire_send_turn_retries_and_recovers_on_transient_error():
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        transport = WireTransport(bundle)

        mock_send = AsyncMock()
        mock_send.side_effect = [
            ConnectionResetError("Socket dropped"),
            TurnResult(text="Recovered text", conversation_id="c1", message_id="m1", model="gpt-5-6-thinking", duration_ms=100)
        ]

        with patch.object(transport, "_send_once", mock_send):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                res = await transport.send_turn(prompt="test", max_retries=3)
                assert res.text == "Recovered text"
                assert mock_send.call_count == 2

    asyncio.run(_run())


def test_wire_never_replays_prompt_after_a_tool_has_started():
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        transport = WireTransport(bundle)

        async def tool_then_disconnect(**kwargs):
            kwargs["on_event"](TelemetryEvent(event_type="tool_call_started", data={"id": "call-1"}))
            raise ConnectionResetError("stream lost after tool dispatch")

        with patch.object(transport, "_send_once", side_effect=tool_then_disconnect) as mock_send:
            with pytest.raises(ConnectionResetError):
                await transport.send_turn(prompt="must-not-replay", max_retries=3)
            assert mock_send.call_count == 1

    asyncio.run(_run())

def test_wire_send_turn_triggers_lifebuoy_on_401():
    async def _run():
        import base64
        import json
        import time
        expired_payload = base64.urlsafe_b64encode(json.dumps({"exp": int(time.time()) - 3600}).encode()).decode().rstrip("=")
        expired_token = f"eyJhbGciOiJIUzI1NiJ9.{expired_payload}.signature"
        bundle = TokenBundle(access_token=expired_token, oai_device_id="dev")
        transport = WireTransport(bundle)

        mock_send = AsyncMock()
        mock_send.side_effect = [
            RuntimeError("ChatGPT Web POST returned HTTP 401: Unauthorized"),
            TurnResult(text="Success after lifebuoy", conversation_id="c1", message_id="m1", model="gpt-5-6-thinking", duration_ms=120)
        ]

        mock_lifebuoy = AsyncMock()
        mock_lifebuoy.remint_credentials = AsyncMock(return_value=TokenBundle(access_token="fresh", oai_device_id="dev"))

        with patch.object(transport, "_send_once", mock_send):
            with patch("gpt_cli.transport.lifebuoy.BrowserLifebuoy", return_value=mock_lifebuoy):
                with patch("asyncio.sleep", new_callable=AsyncMock):
                    res = await transport.send_turn(prompt="test", max_retries=3)
                    assert res.text == "Success after lifebuoy"
                    assert mock_send.call_count == 2
                    assert transport.bundle.access_token == "fresh"

    asyncio.run(_run())

def test_wire_empty_text_marks_failed_and_preserves_parent_id():
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        transport = WireTransport(bundle)

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def fake_stream():
            yield b'data: {"message": {"id": "th_1", "author": {"role": "assistant"}, "content": {"content_type": "thoughts", "thoughts": [{"summary": "Thinking"}]}}}\n\n'
            yield b'data: [DONE]\n\n'

        mock_resp.aiter_content = fake_stream

        with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as mock_prep:
            mock_prep.return_value = ({}, "s1", "t1", None)
            with patch.object(transport.session, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = mock_resp
                res = await transport.send_turn(prompt="test", parent_id="valid_parent_123", max_retries=1)
                assert res.text == ""
                assert res.status == "failed"
                assert res.message_id == "valid_parent_123", "Must preserve parent_id when text is empty"

    asyncio.run(_run())


def test_wire_waits_for_every_correlated_tool_after_done_marker():
    """A completion marker cannot close a turn while another tool is active."""
    async def _run():
        bundle = TokenBundle(access_token="fake", oai_device_id="dev")
        transport = WireTransport(bundle)

        frames = [
            {"message": {"id": "tool-a", "author": {"role": "assistant"}, "recipient": "vendor.first", "content": {"content_type": "text", "parts": ["{}"]}, "status": "in_progress"}},
            {"message": {"id": "tool-b", "author": {"role": "assistant"}, "recipient": "vendor.second", "content": {"content_type": "text", "parts": ["{}"]}, "status": "in_progress"}},
            {"message": {"id": "result-a", "parent": "tool-a", "author": {"role": "tool"}, "content": {"content_type": "text", "parts": ["first complete"]}}},
            {"message": {"id": "final-a", "author": {"role": "assistant"}, "recipient": "all", "channel": "final", "content": {"content_type": "text", "parts": ["answer"]}}},
            "[DONE]",
            {"message": {"id": "result-b", "parent": "tool-b", "author": {"role": "tool"}, "content": {"content_type": "text", "parts": ["second complete"]}}},
        ]

        class MockResponse:
            status_code = 200

            async def aiter_content(self):
                for frame in frames:
                    payload = frame if isinstance(frame, str) else json.dumps(frame)
                    yield f"data: {payload}\n\n".encode()

        events = []
        with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as mock_prep:
            mock_prep.return_value = ({}, "s1", "t1", None)
            with patch.object(transport.session, "post", new_callable=AsyncMock) as mock_post:
                mock_post.return_value = MockResponse()
                result = await transport._send_once(prompt="test", on_event=events.append)

        finished = [event.data["id"] for event in events if event.event_type == "tool_call_finished"]
        assert finished == ["tool-a", "tool-b"]
        assert result.status == "completed"
        assert result.text == "answer"

    asyncio.run(_run())
