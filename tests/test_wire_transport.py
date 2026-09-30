import pytest
from gpt_cli.core.types import TokenBundle
from gpt_cli.models.registry import ModelRegistry
from gpt_cli.transport.wire import WireTransport

def test_model_registry_resolution():
    model = ModelRegistry.resolve("5.6")
    assert model.slug == "gpt-5-6-thinking"
    assert model.default_effort == "extended"

    sol_model = ModelRegistry.resolve("sol")
    assert sol_model.slug == "gpt-5-6-thinking"

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

@pytest.mark.asyncio
async def test_wire_incomplete_stream_marked_properly():
    bundle = TokenBundle(access_token="fake", oai_device_id="dev")
    transport = WireTransport(bundle)

    class MockResponse:
        status_code = 200
        text = "OK"

        async def aiter_content(self):
            # Emits partial text then abruptly aborts without [DONE] or terminal status
            chunk = b'data: {"message": {"id": "m1", "author": {"role": "assistant"}, "content": {"content_type": "text", "parts": ["Partial response..."]}, "channel": "final"}}\n\n'
            yield chunk
            raise ConnectionResetError("Connection lost mid-stream")

    from unittest.mock import AsyncMock, patch
    from gpt_cli.transport.conduit import ConduitHandshake
    with patch.object(transport.session, "post", new_callable=AsyncMock) as mock_post:
        with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as mock_prep:
            mock_prep.return_value = ({}, "s_id", "t_id", None)
            mock_post.return_value = MockResponse()

            result = await transport._send_once(prompt="hi")
            assert result.status == "incomplete"
            assert result.text == "Partial response..."
            assert "Connection lost" in str(result.error)

@pytest.mark.asyncio
async def test_wire_http_error_text_property_extraction():
    bundle = TokenBundle(access_token="fake", oai_device_id="dev")
    transport = WireTransport(bundle)

    class MockResponseError:
        status_code = 404
        text = '{"detail": "Resource not found"}'

    from unittest.mock import AsyncMock, patch
    from gpt_cli.transport.conduit import ConduitHandshake
    with patch.object(transport.session, "post", new_callable=AsyncMock) as mock_post:
        with patch.object(ConduitHandshake, "prepare_turn", new_callable=AsyncMock) as mock_prep:
            mock_prep.return_value = ({}, "s_id", "t_id", None)
            mock_post.return_value = MockResponseError()

            with pytest.raises(RuntimeError) as exc_info:
                await transport._send_once(prompt="hi")
            assert "HTTP 404" in str(exc_info.value)
            assert "Resource not found" in str(exc_info.value)
