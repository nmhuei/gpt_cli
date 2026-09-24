import pytest
from starlette.testclient import TestClient
from pathlib import Path
from gpt_cli.server.app import create_app
from gpt_cli.session.registry import SessionRegistry
from gpt_cli.cli.main import build_cli_parser

def test_gateway_health():
    app = create_app()
    client = TestClient(app)
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["model"] == "gpt-5-6-thinking"
    assert data["transport"] == "wire_conduit"

def test_gateway_models():
    app = create_app()
    client = TestClient(app)
    res = client.get("/v1/models")
    assert res.status_code == 200
    assert len(res.json()["data"]) >= 1

def test_gateway_messages_empty_validation():
    app = create_app()
    client = TestClient(app)
    res = client.post("/v1/messages", json={"model": "gpt-5-6-thinking", "messages": []})
    assert res.status_code == 400
    assert "Messages cannot be empty" in res.json()["error"]["message"]

def test_cli_parser_run_command():
    parser = build_cli_parser()
    args = parser.parse_args(["run", "-p", "Test prompt", "--json", "-s", "sess_custom", "--new"])
    assert args.command == "run"
    assert args.prompt == "Test prompt"
    assert args.json is True
    assert args.session == "sess_custom"
    assert args.new is True

def test_cli_parser_session_command():
    parser = build_cli_parser()
    args = parser.parse_args(["session", "list", "--json"])
    assert args.command == "session"
    assert args.session_action == "list"
    assert args.json is True

def test_cli_parser_gateway_command():
    parser = build_cli_parser()
    args = parser.parse_args(["start", "--port", "18000"])
    assert args.command == "start"
    assert args.port == 18000
