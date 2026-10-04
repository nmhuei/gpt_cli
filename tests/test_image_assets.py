import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gpt_cli.core.types import TokenBundle
from gpt_cli.telemetry.events import TelemetryEvent
from gpt_cli.telemetry.parser import SSETelemetryParser
from gpt_cli.transport.conversation import ConversationRecovery
from gpt_cli.transport.image_assets import asset_id_from_pointer, download_image_asset
from gpt_cli.transport.conduit import ConduitHandshake
from gpt_cli.transport.wire import WireTransport


def test_image_tool_result_emits_asset_pointer_event():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "image_tool_result",
            "author": {"role": "tool", "name": "image_tool"},
            "recipient": "all",
            "content": {
                "content_type": "multimodal_text",
                "parts": [
                    {
                        "content_type": "image_asset_pointer",
                        "asset_pointer": "sediment://file_abc123",
                        "mime_type": "image/png",
                        "width": 640,
                        "height": 480,
                    }
                ],
            },
        }
    }

    events = parser.feed(f"data: {json.dumps(frame)}\n\n")

    image_events = [event for event in events if event.event_type == "image_artifact"]
    assert len(image_events) == 1
    assert image_events[0].data == {
        "id": "sediment://file_abc123",
        "asset_pointer": "sediment://file_abc123",
        "mime_type": "image/png",
        "width": 640,
        "height": 480,
    }


def test_user_uploaded_image_is_not_reported_as_generated_artifact():
    parser = SSETelemetryParser()
    frame = {
        "message": {
            "id": "user_with_image",
            "author": {"role": "user"},
            "content": {
                "content_type": "multimodal_text",
                "parts": [{
                    "content_type": "image_asset_pointer",
                    "asset_pointer": "sediment://file_input123",
                    "mime_type": "image/png",
                }],
            },
        }
    }

    events = parser.feed(f"data: {json.dumps(frame)}\n\n")

    assert not any(event.event_type == "image_artifact" for event in events)


def test_v1_patch_stream_can_append_an_image_artifact_after_tool_snapshot():
    parser = SSETelemetryParser()
    tool_snapshot = {
        "message": {
            "id": "image_tool_result",
            "author": {"role": "tool", "name": "image_tool"},
            "content": {"content_type": "multimodal_text", "parts": []},
        }
    }
    append_part = {
        "o": "patch",
        "v": [{
            "p": "/message/content/parts/-",
            "o": "append",
            "v": {
                "content_type": "image_asset_pointer",
                "asset_pointer": "sediment://file_patch123",
                "mime_type": "image/png",
            },
        }],
    }

    events = parser.feed(
        f"data: {json.dumps(tool_snapshot)}\n\n"
        f"data: {json.dumps(append_part)}\n\n"
    )

    image_events = [event for event in events if event.event_type == "image_artifact"]
    assert len(image_events) == 1
    assert image_events[0].data["asset_pointer"] == "sediment://file_patch123"


def test_conversation_recovery_exposes_generated_image_pointer_in_branch_timeline():
    async def _run():
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "current_node": "final",
            "mapping": {
                "user": {"parent": "root", "message": {"author": {"role": "user"}}},
                "image_tool": {
                    "parent": "user",
                    "message": {
                        "id": "image_tool",
                        "author": {"role": "tool", "name": "image_tool"},
                        "content": {
                            "content_type": "multimodal_text",
                            "parts": [{
                                "content_type": "image_asset_pointer",
                                "asset_pointer": "sediment://file_abc123",
                                "mime_type": "image/png",
                                "width": 640,
                                "height": 480,
                            }],
                        },
                    },
                },
                "final": {
                    "parent": "image_tool",
                    "message": {
                        "author": {"role": "assistant"},
                        "recipient": "all",
                        "content": {"content_type": "text", "parts": ["done"]},
                    },
                },
            },
        }
        session.get = AsyncMock(return_value=response)

        state = await ConversationRecovery(TokenBundle("fake"), session=session).fetch(
            "conv_1", expected_user_message_id="user"
        )

        image_events = [event for event in state.timeline if event.event_type == "image_artifact"]
        assert len(image_events) == 1
        assert image_events[0].data["asset_pointer"] == "sediment://file_abc123"

    asyncio.run(_run())


def test_fresh_image_turn_can_recover_artifact_when_server_rekeys_user_message():
    """Image generation may replace the client UUID with a server node UUID."""
    async def _run():
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "current_node": "final",
            "mapping": {
                "server_user": {"parent": "client-created-root", "message": {"author": {"role": "user"}}},
                "image_tool": {
                    "parent": "server_user",
                    "message": {
                        "author": {"role": "tool", "name": "image_tool"},
                        "content": {"content_type": "multimodal_text", "parts": [{
                            "content_type": "image_asset_pointer",
                            "asset_pointer": "sediment://file_rekey123",
                            "mime_type": "image/png",
                        }]},
                    },
                },
                "final": {
                    "parent": "image_tool",
                    "message": {"author": {"role": "assistant"}, "content": {"content_type": "text", "parts": []}},
                },
            },
        }
        session.get = AsyncMock(return_value=response)

        state = await ConversationRecovery(TokenBundle("fake"), session=session).fetch(
            "conv_1",
            expected_user_message_id="client_uuid_that_was_rekeyed",
            allow_current_branch=True,
        )

        assert state.matches_expected_message is False
        assert [event.data["asset_pointer"] for event in state.timeline if event.event_type == "image_artifact"] == [
            "sediment://file_rekey123"
        ]

    asyncio.run(_run())


def test_fresh_image_turn_polls_until_async_artifact_is_written():
    async def _run():
        session = MagicMock()

        def response(parts):
            value = MagicMock(status_code=200)
            value.json.return_value = {
                "current_node": "image_tool",
                "mapping": {
                    "server_user": {"parent": "client-created-root", "message": {"author": {"role": "user"}}},
                    "image_tool": {
                        "parent": "server_user",
                        "message": {
                            "author": {"role": "tool", "name": "image_tool"},
                            "content": {"content_type": "multimodal_text", "parts": parts},
                        },
                    },
                },
            }
            return value

        session.get = AsyncMock(side_effect=[
            response([]),
            response([{
                "content_type": "image_asset_pointer",
                "asset_pointer": "sediment://file_async123",
                "mime_type": "image/png",
            }]),
        ])
        state = await ConversationRecovery(TokenBundle("fake"), session=session).fetch_until_image_artifact(
            "conv_1", expected_user_message_id="rekeyed", interval_seconds=0, max_attempts=2
        )

        assert session.get.await_count == 2
        assert [event.data["asset_pointer"] for event in state.timeline if event.event_type == "image_artifact"] == [
            "sediment://file_async123"
        ]

    asyncio.run(_run())


def test_asset_id_parser_accepts_observed_sediment_pointer_and_rejects_paths():
    assert asset_id_from_pointer("sediment://file_abc123") == "file_abc123"
    assert asset_id_from_pointer("file-service://file-abc123") == "file-abc123"

    for bad_pointer in ("https://example.test/image.png", "sediment://../../secret", "file_abc123"):
        try:
            asset_id_from_pointer(bad_pointer)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unsafe or unsupported pointer accepted: {bad_pointer}")


def test_download_saves_image_to_configured_directory_and_uses_account_headers(tmp_path: Path):
    async def _run():
        session = MagicMock()
        descriptor = MagicMock(
            status_code=200,
            headers={"content-type": "application/json"},
        )
        descriptor.json.return_value = {
            "download_url": "https://chatgpt.com/backend-api/estuary/content?id=file_abc123&sig=hidden"
        }
        content_response = SimpleNamespace(
            status_code=200,
            content=b"fake-png-bytes",
            headers={"content-type": "image/png"},
        )
        session.get = AsyncMock(side_effect=[descriptor, content_response])
        bundle = TokenBundle(
            access_token="access-token",
            cookies={"session": "cookie-value"},
            oai_device_id="device-id",
            chatgpt_account_id="account-id",
        )

        path = await download_image_asset(
            session,
            bundle,
            {
                "asset_pointer": "sediment://file_abc123",
                "mime_type": "image/png",
            },
            output_dir=tmp_path,
        )

        assert path == tmp_path / "gpt-image-file_abc123.png"
        assert path.read_bytes() == b"fake-png-bytes"
        first_call, second_call = session.get.call_args_list
        assert first_call.kwargs["headers"]["Authorization"] == "Bearer access-token"
        assert first_call.kwargs["headers"]["ChatGPT-Account-ID"] == "account-id"
        assert first_call.kwargs["headers"]["x-openai-target-path"] == "/backend-api/files/download/file_abc123"
        assert second_call.args[0].startswith("https://chatgpt.com/backend-api/estuary/content?")
        assert second_call.kwargs["headers"]["x-openai-target-path"] == "/backend-api/estuary/content"
        assert session.get.await_count == 2

    asyncio.run(_run())


def test_download_rejects_untrusted_descriptor_url(tmp_path: Path):
    async def _run():
        session = MagicMock()
        descriptor = MagicMock(status_code=200, headers={"content-type": "application/json"})
        descriptor.json.return_value = {"download_url": "https://attacker.invalid/image.png"}
        session.get = AsyncMock(return_value=descriptor)

        try:
            await download_image_asset(
                session,
                TokenBundle("fake"),
                {"asset_pointer": "sediment://file_badurl", "mime_type": "image/png"},
                output_dir=tmp_path,
            )
        except RuntimeError as exc:
            assert "unexpected URL" in str(exc)
        else:
            raise AssertionError("untrusted download URL was accepted")
        assert session.get.await_count == 1
        assert not list(tmp_path.iterdir())

    asyncio.run(_run())


def test_wire_turn_downloads_image_artifacts_seen_in_sse(tmp_path: Path):
    async def _run():
        transport = WireTransport(TokenBundle(access_token="fake"))

        class Response:
            status_code = 200

            async def aiter_content(self):
                image_frame = {
                    "conversation_id": "conv_image",
                    "message": {
                        "id": "image_result",
                        "author": {"role": "tool", "name": "image_tool"},
                        "content": {
                            "content_type": "multimodal_text",
                            "parts": [{
                                "content_type": "image_asset_pointer",
                                "asset_pointer": "sediment://file_sse123",
                                "mime_type": "image/png",
                            }],
                        },
                    },
                }
                yield f"data: {json.dumps(image_frame)}\n\n".encode()
                yield b'data: {"type":"message_stream_complete"}\n\n'

        expected_path = tmp_path / "gpt-image-file_sse123.png"
        with patch.object(
            ConduitHandshake, "prepare_turn", return_value=({}, "s", "t", None)
        ):
            transport.session.post = AsyncMock(return_value=Response())
            with patch(
                "gpt_cli.transport.image_assets.download_image_asset",
                new=AsyncMock(return_value=expected_path),
            ) as downloader:
                result = await transport._send_once("draw", on_event=lambda _event: None)

        assert result.image_paths == [str(expected_path)]
        assert downloader.await_count == 1
        assert downloader.await_args.args[2]["asset_pointer"] == "sediment://file_sse123"
        await transport.close()

    asyncio.run(_run())


def test_image_command_uses_dag_to_find_asset_missing_from_sse(tmp_path: Path):
    async def _run():
        transport = WireTransport(TokenBundle(access_token="fake"))

        class Response:
            status_code = 200

            async def aiter_content(self):
                yield b'data: {"conversation_id":"conv_dag","message":{"id":"answer","author":{"role":"assistant"},"recipient":"all","channel":"final","content":{"content_type":"text","parts":["done"]},"status":"finished_successfully"}}\n\n'
                yield b'data: {"type":"message_stream_complete"}\n\n'

        state = SimpleNamespace(
            matches_expected_message=True,
            timeline=(TelemetryEvent("image_artifact", {
                "id": "sediment://file_dag123",
                "asset_pointer": "sediment://file_dag123",
                "mime_type": "image/png",
            }),),
        )
        expected_path = tmp_path / "gpt-image-file_dag123.png"
        with patch.object(
            ConduitHandshake, "prepare_turn", return_value=({}, "s", "t", None)
        ):
            transport.session.post = AsyncMock(return_value=Response())
            with patch(
                "gpt_cli.transport.conversation.ConversationRecovery.fetch",
                new=AsyncMock(return_value=state),
            ) as fetch_dag:
                with patch(
                    "gpt_cli.transport.image_assets.download_image_asset",
                    new=AsyncMock(return_value=expected_path),
                ):
                    result = await transport._send_once("draw", capture_images=True)

        assert result.image_paths == [str(expected_path)]
        assert fetch_dag.await_count == 1
        assert fetch_dag.await_args.kwargs["expected_user_message_id"]
        await transport.close()

    asyncio.run(_run())
