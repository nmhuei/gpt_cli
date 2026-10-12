import json
import pytest
from pathlib import Path
from gpt_cli.models.schemas import ModelDefinition
from gpt_cli.models.registry import ModelRegistry
from gpt_cli.models.fetcher import parse_upstream_models_response


def test_model_definition_serialization():
    model = ModelDefinition(
        slug="gpt-6-thinking",
        display_name="GPT-6",
        default_effort="extended",
        supports_thinking=True,
        thinking_efforts=("standard", "extended"),
        reasoning_type="reasoning",
        description="Next-gen reasoning model",
    )
    data = model.to_dict()
    assert data["slug"] == "gpt-6-thinking"
    assert data["thinking_efforts"] == ["standard", "extended"]

    restored = ModelDefinition.from_dict(data)
    assert restored.slug == model.slug
    assert restored.supports_thinking is True
    assert restored.thinking_efforts == ("standard", "extended")


def test_pick_flagship_model():
    models = [
        ModelDefinition(slug="gpt-5-5-mini", display_name="GPT-5.5 Mini", supports_thinking=False, thinking_efforts=(), reasoning_type="none"),
        ModelDefinition(slug="gpt-5-6-thinking", display_name="GPT-5.6 Sol", supports_thinking=True, thinking_efforts=("standard", "extended"), reasoning_type="reasoning"),
        ModelDefinition(slug="gpt-6-instant", display_name="GPT-6 Instant", supports_thinking=False, thinking_efforts=(), reasoning_type="none"),
        ModelDefinition(slug="gpt-6-thinking", display_name="GPT-6", supports_thinking=True, thinking_efforts=("standard", "extended"), reasoning_type="reasoning"),
        ModelDefinition(slug="gpt-6-mini", display_name="GPT-6 Mini", supports_thinking=False, thinking_efforts=(), reasoning_type="none"),
        ModelDefinition(slug="gpt-6.1-sol-wm", display_name="GPT-6.1 Sol WM", supports_thinking=True, thinking_efforts=("standard", "extended"), reasoning_type="reasoning"),
    ]
    flagship = ModelRegistry.pick_flagship_model(models)
    assert flagship.slug == "gpt-6-thinking"
    assert flagship.default_effort == "extended"


def test_default_model_is_gpt_6_thinking():
    assert ModelRegistry.DEFAULT_SLUG == "gpt-6-thinking"
    assert ModelRegistry.resolve().slug == "gpt-6-thinking"
    assert ModelRegistry.resolve("default").slug == "gpt-6-thinking"
    assert ModelRegistry.resolve("thinking").slug == "gpt-6-thinking"


def test_parse_upstream_models_response():
    raw_payload = {
        "models": [
            {
                "slug": "gpt-6-thinking",
                "title": "GPT-6",
                "description": "Our latest flagship model",
                "reasoning_type": "reasoning",
                "configurable_thinking_effort": True,
                "thinking_efforts": [
                    {"thinking_effort": "standard", "full_label": "Standard"},
                    {"thinking_effort": "extended", "full_label": "Extended"},
                ],
            },
            {
                "slug": "gpt-6",
                "title": "GPT-6 Instant",
                "reasoning_type": "auto",
                "configurable_thinking_effort": False,
                "thinking_efforts": [],
            },
            {
                "slug": "gpt-6.1-sol-wm",
                "title": "GPT-6.1 Sol WM",
                "is_work_mode_model": True,
                "reasoning_type": "reasoning",
                "configurable_thinking_effort": True,
                "thinking_efforts": [{"thinking_effort": "extended"}],
            },
        ]
    }
    parsed = parse_upstream_models_response(raw_payload)
    # Work-mode models should be filtered out for chat
    assert len(parsed) == 2
    assert parsed[0].slug == "gpt-6-thinking"
    assert parsed[0].supports_thinking is True
    assert parsed[0].thinking_efforts == ("standard", "extended")
    assert parsed[1].slug == "gpt-6"
    assert parsed[1].supports_thinking is False
    assert "gpt-6.1-sol-wm" not in [m.slug for m in parsed]


def test_model_registry_cache_and_alias_resolution(tmp_path: Path):
    cache_file = tmp_path / "models_cache.json"
    models = [
        ModelDefinition(slug="gpt-6-thinking", display_name="GPT-6", supports_thinking=True, thinking_efforts=("standard", "extended")),
        ModelDefinition(slug="gpt-6", display_name="GPT-6 Instant", supports_thinking=False, thinking_efforts=()),
    ]
    ModelRegistry.save_cache(models, cache_file)
    assert cache_file.is_file()

    # Reset in-memory dynamic models
    ModelRegistry.clear_dynamic()
    loaded = ModelRegistry.load_cache(cache_file)
    assert len(loaded) >= 2

    # Check aliases
    assert ModelRegistry.resolve("gpt-6-thinking").slug == "gpt-6-thinking"
    assert ModelRegistry.resolve("6-thinking").slug == "gpt-6-thinking"
    assert ModelRegistry.resolve("6t").slug == "gpt-6-thinking"
    assert ModelRegistry.resolve("6").slug == "gpt-6"


def test_build_suggested_command():
    from gpt_cli.cli.model_command import build_suggested_command

    thinking_flagship = ModelDefinition(
        slug="gpt-6-thinking",
        display_name="GPT-6",
        supports_thinking=True,
        thinking_efforts=("standard", "extended"),
    )
    run_cmd, chat_cmd = build_suggested_command(thinking_flagship)
    assert run_cmd == 'gpt run -m gpt-6-thinking -e extended -p "<prompt>"'
    assert chat_cmd == "gpt chat -m gpt-6-thinking -e extended"

    instant_model = ModelDefinition(
        slug="gpt-6",
        display_name="GPT-6 Instant",
        supports_thinking=False,
        thinking_efforts=(),
    )
    run_cmd_inst, chat_cmd_inst = build_suggested_command(instant_model)
    assert run_cmd_inst == 'gpt run -m gpt-6 -p "<prompt>"'
    assert chat_cmd_inst == "gpt chat -m gpt-6"


@pytest.mark.asyncio
async def test_run_model_command_offline_json(capsys):
    import argparse
    from gpt_cli.cli.model_command import run_model_command

    args = argparse.Namespace(offline=True, json=True)
    ret = await run_model_command(args)
    assert ret == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "models" in data
    assert "flagship" in data
    assert "suggested_command" in data
    assert data["flagship"]["slug"] in [m["slug"] for m in data["models"]]


@pytest.mark.asyncio
async def test_run_model_command_offline_text(capsys):
    import argparse
    from gpt_cli.cli.model_command import run_model_command

    args = argparse.Namespace(offline=True, json=False)
    ret = await run_model_command(args)
    assert ret == 0

    captured = capsys.readouterr()
    assert "Supported ChatGPT Models" in captured.out
    assert "Model mạnh nhất hiện tại" in captured.out
    assert "Lệnh đề xuất để chạy" in captured.out

