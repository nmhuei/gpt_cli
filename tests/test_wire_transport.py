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
