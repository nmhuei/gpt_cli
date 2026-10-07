import argparse
import asyncio
import pytest

from gpt_cli.core.types import TokenBundle, TurnResult


def test_main_handle_run_passes_explicit_dependencies(monkeypatch):
    import gpt_cli.cli.main as main

    seen = {}

    async def fake_run(args, **kwargs):
        seen.update(kwargs)
        return TurnResult(text="ok", conversation_id="c", message_id="m", model="gpt-5-6-thinking")

    monkeypatch.setattr(main, "_run_command", fake_run)
    asyncio.run(main.handle_run(argparse.Namespace()))
    assert "resolve_connector_context" in seen
    assert "defaults" in seen
    assert "token_cache" in seen
