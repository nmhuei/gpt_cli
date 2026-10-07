from gpt_cli.config.settings import Settings


def test_bqa_keyword_reads_bqa_tunnel_file(tmp_path, monkeypatch):
    tunnel = tmp_path / "tunnel_url.txt"
    tunnel.write_text("https://abc.trycloudflare.com\n", encoding="utf-8")
    monkeypatch.setenv("BQA_TUNNEL_FILE", str(tunnel))
    assert Settings().resolve_mcp_url("bqa") == "https://abc.trycloudflare.com/mcp"
    assert Settings().resolve_mcp_url("BQA") == "https://abc.trycloudflare.com/mcp"


def test_bqa_keyword_missing_file_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("BQA_TUNNEL_FILE", str(tmp_path / "missing.txt"))
    try:
        Settings().resolve_mcp_url("bqa")
    except ValueError as exc:
        assert "BQA tunnel URL not found" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_explicit_url_is_kept():
    assert Settings().resolve_mcp_url("https://x.example/mcp") == "https://x.example/mcp"
