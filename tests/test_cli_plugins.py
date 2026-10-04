import json
from unittest.mock import MagicMock

from gpt_cli.plugins.manager import PluginInfo


def test_plugin_add_creates_links_and_reports_live_identifiers(monkeypatch, capsys):
    """The CLI must expose the complete create → link → permission operation."""
    from gpt_cli.cli.main import build_cli_parser, handle_plugin

    manager = MagicMock()
    manager.add_mcp_plugin.return_value = PluginInfo(
        link_id="link_123",
        connector_id="asdk_app_123",
        name="hello",
        apps_privacy_control="full_access",
        actions=["health_check"],
        auth_status="ACTIVE",
    )
    monkeypatch.setattr("gpt_cli.cli.main.PluginManager", lambda: manager)
    args = build_cli_parser().parse_args([
        "plugin", "add", "hello", "https://example.test/mcp",
        "--description", "test MCP", "--permission", "full_access", "--json",
    ])

    handle_plugin(args)

    manager.add_mcp_plugin.assert_called_once_with(
        name="hello",
        mcp_url="https://example.test/mcp",
        description="test MCP",
        permission="full_access",
    )
    out = json.loads(capsys.readouterr().out)
    assert out == {
        "status": "added",
        "name": "hello",
        "connector_id": "asdk_app_123",
        "link_id": "link_123",
        "auth_status": "ACTIVE",
        "permission": "full_access",
        "actions": ["health_check"],
    }
