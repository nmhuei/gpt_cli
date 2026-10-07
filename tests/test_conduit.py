import asyncio
from unittest.mock import AsyncMock, patch

from gpt_cli.core.types import TokenBundle
from gpt_cli.transport.conduit import ConduitHandshake


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_integrity_headers_use_one_web_account_identity():
    headers = ConduitHandshake.build_integrity_headers(
        TokenBundle(access_token="token", oai_device_id="device", chatgpt_account_id="account"),
        "session",
        "trace",
    )

    assert [key for key in headers if key.lower() == "chatgpt-account-id"] == ["ChatGPT-Account-ID"]
    assert "oai-did" not in headers
    assert "originator" not in headers
    assert "x-openai-codex-window-type" not in headers
    assert "x-openai-web-frontend" not in headers


def test_prepare_turn_uses_final_requirements_token_without_finalize():
    async def run():
        calls = []

        async def post(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/sentinel/chat-requirements/prepare"):
                return _Response(200, {"token": "requirements"})
            if url.endswith("/f/conversation/prepare"):
                return _Response(200, {"conduit_token": "conduit"})
            raise AssertionError(f"unexpected endpoint: {url}")

        session = AsyncMock()
        session.post = post
        bundle = TokenBundle(access_token="token", oai_device_id="device")
        with patch("gpt_cli.transport.conduit.bootstrap_proof_token", return_value="bootstrap"):
            headers, *_ = await ConduitHandshake.prepare_turn(session, bundle, "gpt-5-6-thinking")

        assert len(calls) == 2
        assert "openai-sentinel-chat-requirements-token" in calls[1][1]["headers"]
        assert "openai-sentinel-chat-requirements-prepare-token" not in calls[1][1]["headers"]
        assert "openai-sentinel-chat-requirements-token" in headers

    asyncio.run(run())


def test_prepare_turn_finalizes_prepare_token_before_conduit_prepare():
    async def run():
        calls = []

        async def post(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/sentinel/chat-requirements/prepare"):
                return _Response(200, {"prepare_token": "prepare"})
            if url.endswith("/sentinel/chat-requirements/finalize"):
                return _Response(200, {"token": "requirements"})
            if url.endswith("/f/conversation/prepare"):
                return _Response(200, {"conduit_token": "conduit"})
            raise AssertionError(f"unexpected endpoint: {url}")

        session = AsyncMock()
        session.post = post
        bundle = TokenBundle(access_token="token", oai_device_id="device")
        with patch("gpt_cli.transport.conduit.bootstrap_proof_token", return_value="bootstrap"):
            headers, *_ = await ConduitHandshake.prepare_turn(session, bundle, "gpt-5-6-thinking")

        assert calls[1][1]["json"] == {"prepare_token": "prepare"}
        assert "openai-sentinel-chat-requirements-token" in calls[2][1]["headers"]
        assert "openai-sentinel-chat-requirements-prepare-token" not in calls[2][1]["headers"]
        assert "openai-sentinel-chat-requirements-token" in headers

    asyncio.run(run())


def test_prepare_turn_passes_thinking_effort_to_conduit_prepare():
    async def run():
        calls = []

        async def post(url, **kwargs):
            calls.append((url, kwargs))
            if url.endswith("/sentinel/chat-requirements/prepare"):
                return _Response(200, {"token": "requirements"})
            if url.endswith("/f/conversation/prepare"):
                return _Response(200, {"conduit_token": "conduit"})
            raise AssertionError(f"unexpected endpoint: {url}")

        session = AsyncMock()
        session.post = post
        bundle = TokenBundle(access_token="token", oai_device_id="device")
        with patch("gpt_cli.transport.conduit.bootstrap_proof_token", return_value="bootstrap"):
            headers, *_ = await ConduitHandshake.prepare_turn(
                session, bundle, "gpt-5-6-thinking", thinking_effort="extended"
            )

        assert calls[1][1]["json"]["thinking_effort"] == "extended"

    asyncio.run(run())


def test_prepare_turn_redacts_sensitive_error_bodies():
    async def run():
        class ErrorResponse:
            status_code = 403
            text = "Bearer very-secret-token accessToken=also-secret"

        session = AsyncMock()
        session.post = AsyncMock(return_value=ErrorResponse())
        bundle = TokenBundle(access_token="token", oai_device_id="device")
        with patch("gpt_cli.transport.conduit.bootstrap_proof_token", return_value="bootstrap"):
            try:
                await ConduitHandshake.prepare_turn(session, bundle, "gpt-5-6-thinking")
            except RuntimeError as exc:
                assert "very-secret-token" not in str(exc)
                assert "also-secret" not in str(exc)
                assert "redacted" in str(exc)
            else:
                raise AssertionError("expected prepare_turn failure")

    asyncio.run(run())


def test_prepare_turn_redacts_representative_secret_error_patterns():
    async def run():
        patterns = [
            "Authorization: Bearer hidden-auth-value",
            '{"token":"hidden-json-token"}',
            "session-token=hidden-session-token",
            "X-API-Key: hidden-api-key",
            "cookie=session=hidden-cookie-value",
        ]

        bundle = TokenBundle(access_token="token", oai_device_id="device")
        for body in patterns:
            class ErrorResponse:
                status_code = 403
                text = body

            session = AsyncMock()
            session.post = AsyncMock(return_value=ErrorResponse())
            with patch("gpt_cli.transport.conduit.bootstrap_proof_token", return_value="bootstrap"):
                try:
                    await ConduitHandshake.prepare_turn(session, bundle, "gpt-5-6-thinking")
                except RuntimeError as exc:
                    message = str(exc)
                    assert "hidden" not in message
                    assert "redacted" in message
                else:
                    raise AssertionError("expected prepare_turn failure")

    asyncio.run(run())
