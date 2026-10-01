import json
import sys

import pytest
import gpt_cli.cli.main as cli_main
from gpt_cli.core.bridge import resolve_prompt_with_bridge

def test_resolve_prompt_bracket_mention():
    p = "@[br] con song k"
    res = resolve_prompt_with_bridge(p)
    assert res == "@br con song k"

def test_resolve_prompt_classic_at_mention():
    p = "@br con song k"
    res = resolve_prompt_with_bridge(p)
    assert res == "@br con song k"

def test_resolve_prompt_custom_bridge_in_bracket():
    p = "@[ida] decompile sub_401000"
    res = resolve_prompt_with_bridge(p)
    assert res == "@ida decompile sub_401000"

def test_resolve_prompt_custom_bridge_classic():
    p = "@ida decompile sub_401000"
    res = resolve_prompt_with_bridge(p)
    assert res == "@ida decompile sub_401000"

def test_resolve_prompt_empty_after_bracket():
    p = "@[br]"
    res = resolve_prompt_with_bridge(p)
    assert res == "@br"

def test_resolve_prompt_default_bridge_without_mention():
    p = "solve https://test.lab"
    res = resolve_prompt_with_bridge(p, bridge_name="br")
    assert res == "@br solve https://test.lab"


def test_resolve_prompt_binds_workspace_name_for_br():
    res = resolve_prompt_with_bridge("solve /tmp/challenge", bridge_name="br")
    assert res == "@br bind challenge; solve"
    assert len(res.split()) <= 5

def test_resolve_prompt_no_bridge_disabled():
    p = "hello world"
    res = resolve_prompt_with_bridge(p, bridge_name="none")
    assert res == "hello world"

def test_resolve_prompt_no_bridge_off():
    p = "hello world"
    res = resolve_prompt_with_bridge(p, bridge_name="off")
    assert res == "hello world"

def test_resolve_prompt_already_wrapped_no_double():
    p = "[Use MCP connector @[br] for this request]: solve"
    res = resolve_prompt_with_bridge(p, bridge_name="br")
    assert res == "@br solve"


def test_status_json_does_not_shadow_module_json(monkeypatch, capsys):
    monkeypatch.setattr(cli_main, "status_daemon", lambda: {"running": False})
    monkeypatch.setattr(sys, "argv", ["gpt", "status", "--json"])

    cli_main.main()

    assert json.loads(capsys.readouterr().out) == {"running": False}
