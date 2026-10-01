from pathlib import Path

from gpt_cli.config.settings import Settings


def test_mcp_tunnel_default_uses_xdg_config_not_developer_checkout(monkeypatch, tmp_path):
    monkeypatch.delenv("DEFAULT_MCP_TUNNEL_FILE", raising=False)
    monkeypatch.delenv("DEFAULT_MCP_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    settings = Settings()

    assert settings.default_mcp_tunnel_file == Path(tmp_path / "config/gpt_cli/mcp/tunnel_url.txt")


def test_settings_preserve_legacy_webgpt_data_root_without_xdg_override(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("WEBGPT_CACHE_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    settings = Settings()

    assert settings.cache_dir == tmp_path / "home/.local/share/webgpt/profiles/personal"


def test_mcp_url_resolution_prefers_explicit_environment_then_discovery_file(monkeypatch, tmp_path):
    tunnel_file = tmp_path / "mcp" / "tunnel_url.txt"
    tunnel_file.parent.mkdir(parents=True)
    tunnel_file.write_text("https://dynamic.example", encoding="utf-8")
    monkeypatch.setenv("DEFAULT_MCP_TUNNEL_FILE", str(tunnel_file))
    monkeypatch.setenv("DEFAULT_MCP_URL", "https://environment.example/mcp")

    settings = Settings()

    assert settings.resolve_mcp_url("https://explicit.example") == "https://explicit.example"
    assert settings.resolve_mcp_url() == "https://environment.example/mcp"

    monkeypatch.delenv("DEFAULT_MCP_URL")
    assert Settings().resolve_mcp_url() == "https://dynamic.example/mcp"
