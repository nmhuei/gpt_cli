import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from gpt_cli.core.types import TokenBundle, TurnResult
from gpt_cli.transport.admission import UpstreamAdmission, UpstreamCooldownError
from gpt_cli.transport.wire import WireTransport


def test_admission_reserves_staggered_slots_across_processes(tmp_path):
    first = UpstreamAdmission(tmp_path, request_interval_seconds=15.0)
    second = UpstreamAdmission(tmp_path, request_interval_seconds=15.0)

    assert first.reserve(now=100.0) == 0.0
    assert second.reserve(now=100.0) == 15.0


def test_admission_blocks_until_server_cooldown_expires(tmp_path):
    admission = UpstreamAdmission(tmp_path, request_interval_seconds=0.0)
    admission.record_rejection("unusual activity", now=100.0, cooldown_seconds=900.0)

    with pytest.raises(UpstreamCooldownError, match="retry after"):
        admission.reserve(now=101.0)

    assert admission.reserve(now=1000.0) == 0.0


def test_wire_opens_global_cooldown_for_sse_unusual_activity(tmp_path):
    async def run() -> None:
        admission = UpstreamAdmission(tmp_path, request_interval_seconds=0.0)
        transport = WireTransport(TokenBundle(access_token="fake"), admission=admission)
        rejected = TurnResult(
            text="",
            conversation_id="conv_1",
            message_id="",
            model="gpt-5-6-thinking",
            status="failed",
            lifecycle_status="upstream_error",
            error="Upstream SSE error: Our systems have detected unusual activity coming from your system.",
        )
        with patch.object(transport, "_send_once", new_callable=AsyncMock, return_value=rejected):
            result = await transport.send_turn("hi", max_retries=1)

        assert result.lifecycle_status == "upstream_error"
        with pytest.raises(UpstreamCooldownError):
            admission.reserve()

    asyncio.run(run())


def test_wire_does_not_post_while_shared_cooldown_is_active(tmp_path):
    async def run() -> None:
        admission = UpstreamAdmission(tmp_path, request_interval_seconds=0.0)
        admission.record_rejection("unusual activity", cooldown_seconds=900.0)
        transport = WireTransport(TokenBundle(access_token="fake"), admission=admission)

        with patch.object(transport, "_send_once", new_callable=AsyncMock) as send_once:
            result = await transport.send_turn("hi", max_retries=1)

        send_once.assert_not_awaited()
        assert result.lifecycle_status == "upstream_error"
        assert result.status == "failed"
        assert result.error is not None
        assert "Upstream cooldown active" in result.error

    asyncio.run(run())
