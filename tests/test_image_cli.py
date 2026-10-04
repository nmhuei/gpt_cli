import asyncio
from pathlib import Path
import sys

import pytest

from gpt_cli.cli.main import build_cli_parser, handle_image
from gpt_cli.core.types import TurnResult


def test_image_command_requires_prompt_and_supports_json():
    args = build_cli_parser().parse_args(["image", "-p", "a blue bird", "--json"])

    assert args.command == "image"
    assert args.prompt == "a blue bird"
    assert args.json is True


def test_image_help_is_not_routed_as_a_prompt(monkeypatch, capsys):
    from gpt_cli.cli.main import main

    monkeypatch.setattr(sys, "argv", ["gpt", "image", "--help"])
    with pytest.raises(SystemExit) as raised:
        main()

    assert raised.value.code == 0
    assert "usage: gpt image" in capsys.readouterr().out


def test_image_command_runs_fresh_unbridged_quiet_turn_and_reports_saved_paths(monkeypatch, capsys):
    observed = {}
    image_path = Path.home() / "Pictures" / "gpt-images" / "gpt-image-file_abc123.png"

    async def fake_handle_run(args):
        observed.update(vars(args))
        return TurnResult(
            text="Generated.",
            conversation_id="conv_1",
            message_id="msg_1",
            model="gpt-5-6-thinking",
            image_paths=[str(image_path)],
        )

    monkeypatch.setattr("gpt_cli.cli.main.handle_run", fake_handle_run)
    args = build_cli_parser().parse_args(["image", "-p", "a blue bird"])

    asyncio.run(handle_image(args))

    assert observed["prompt"] == "Create an image using the image-generation tool for this description:\n\na blue bird"
    assert observed["no_bridge"] is True
    assert observed["quiet"] is True
    assert observed["resume"] is False
    assert str(image_path) in capsys.readouterr().out


def test_image_command_fails_clearly_when_no_image_artifact_is_returned(monkeypatch, capsys):
    async def fake_handle_run(_args):
        return TurnResult(
            text="I can describe an image, but did not generate one.",
            conversation_id="conv_1",
            message_id="msg_1",
            model="gpt-5-6-thinking",
        )

    monkeypatch.setattr("gpt_cli.cli.main.handle_run", fake_handle_run)
    args = build_cli_parser().parse_args(["image", "-p", "a blue bird"])

    with pytest.raises(SystemExit) as raised:
        asyncio.run(handle_image(args))

    assert raised.value.code == 1
    assert "No image artifact" in capsys.readouterr().err
