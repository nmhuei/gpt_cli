import argparse

from ..config import defaults
from ..config.settings import Settings


def build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gpt",
        description="WebGPT Remake - High-performance Wire Runtime & Gateway"
    )
    subparsers = parser.add_subparsers(dest="command", required=False, metavar="<command>")

    settings = Settings()

    # 1. run
    run_p = subparsers.add_parser("run", help="Run a turn with the model")
    run_p.add_argument("-p", "--prompt", default=None, help="Prompt text (optional if provided as positional arguments)")
    run_p.add_argument("pos_prompt", nargs="*", default=[], help="Positional prompt text")
    run_resume = run_p.add_mutually_exclusive_group()
    run_resume.add_argument("--resume", action="store_true", help="Continue the active local session")
    run_resume.add_argument("-s", "--resume-session", "--session", "--sesion", default=None, help="Continue an existing local session ID")
    run_resume.add_argument("--conversation", default=None, help="Attach a ChatGPT Web conversation UUID")
    run_p.add_argument("-t", "--tag", default=None, help="Tag for the session")
    run_p.add_argument("-m", "--model", default=settings.default_model, help="Model alias")
    run_p.add_argument("-e", "--effort", default=settings.default_effort, help="Thinking effort")
    run_p.add_argument("-b", "--bridge", default=None, help=f"Active MCP bridge/connector name (default: {settings.default_bridge_name})")
    run_p.add_argument("--no-bridge", action="store_true", help="Disable auto-bridge routing")
    run_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # 1b. agent: a bounded continuous session for work that must not end after
    # a single conversational response.  Keep ordinary `run` one-shot.
    agent_p = subparsers.add_parser("agent", help="Run one continuous agent session")
    agent_p.add_argument("-p", "--prompt", default=None, help="Initial task prompt")
    agent_p.add_argument("pos_prompt", nargs="*", default=[], help="Initial task prompt")
    agent_resume = agent_p.add_mutually_exclusive_group()
    agent_resume.add_argument("--resume", action="store_true", help="Continue the active local session")
    agent_resume.add_argument("-s", "--resume-session", "--session", "--sesion", default=None, help="Continue an existing local session ID")
    agent_resume.add_argument("--conversation", default=None, help="Attach a ChatGPT Web conversation UUID")
    agent_p.add_argument("-t", "--tag", default=None, help="Tag for the session")
    agent_p.add_argument("-m", "--model", default=settings.default_model, help="Model alias")
    agent_p.add_argument("-e", "--effort", default=settings.default_effort, help="Thinking effort")
    agent_p.add_argument("-b", "--bridge", default=None, help=f"Active MCP bridge/connector name (default: {settings.default_bridge_name})")
    agent_p.add_argument("--no-bridge", action="store_true", help="Disable auto-bridge routing")
    agent_p.add_argument(
        "--min-runtime-minutes", type=float, default=30.0,
        help="Minimum accumulated upstream turn duration for this session (default: 30)",
    )
    agent_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # Image generation remains a normal conversation turn, but has a focused
    # command and automatically downloads generated image artifacts.
    image_p = subparsers.add_parser("image", help="Generate and save an image")
    image_p.add_argument("-p", "--prompt", required=True, help="Image description")
    image_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # 1c. research: specialized deep research mode
    research_p = subparsers.add_parser("research", help="Run structured deep research on any technical topic or code")
    research_p.add_argument("-p", "--prompt", default=None, help="Research topic or question")
    research_p.add_argument("pos_prompt", nargs="*", default=[], help="Research topic or question")
    research_p.add_argument("--deep", action="store_true", help="Enable multi-phase iterative deep research")
    research_p.add_argument("--turns", type=int, default=None, help="Number of research iterations (default: 3 with --deep, 1 otherwise)")
    research_p.add_argument("-o", "--output", "--report", dest="report_file", default=None, help="Save final synthesized report to markdown file")
    research_resume = research_p.add_mutually_exclusive_group()
    research_resume.add_argument("--resume", action="store_true", help="Continue the active local session")
    research_resume.add_argument("-s", "--resume-session", "--session", "--sesion", default=None, help="Continue an existing local session ID")
    research_resume.add_argument("--conversation", default=None, help="Attach a ChatGPT Web conversation UUID")
    research_p.add_argument("-t", "--tag", default=None, help="Tag for the session")
    research_p.add_argument("-m", "--model", default=settings.default_model, help="Model alias")
    research_p.add_argument("-e", "--effort", default=None, help="Thinking effort")
    research_p.add_argument("-b", "--bridge", default=None, help=f"Active MCP bridge/connector name (default: {settings.default_bridge_name})")
    research_p.add_argument("--no-bridge", action="store_true", help="Disable auto-bridge routing")
    research_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # 2. chat (interactive)
    chat_p = subparsers.add_parser("chat", help="Start interactive terminal chat session")
    chat_resume = chat_p.add_mutually_exclusive_group()
    chat_resume.add_argument("--resume", action="store_true", help="Continue the active local session")
    chat_resume.add_argument("-s", "--resume-session", "--session", "--sesion", default=None, help="Continue an existing local session ID")
    chat_resume.add_argument("--conversation", default=None, help="Attach a ChatGPT Web conversation UUID")
    chat_p.add_argument("-m", "--model", default=settings.default_model, help="Model alias")
    chat_p.add_argument("-b", "--bridge", default=None, help=f"Active MCP bridge/connector name (default: {settings.default_bridge_name})")
    chat_p.add_argument("--no-bridge", action="store_true", help="Disable auto-bridge routing")

    # 3. session
    sess_p = subparsers.add_parser("session", help="Manage tool-created sessions")
    sess_sub = sess_p.add_subparsers(dest="session_action", required=True)
    
    # session list
    list_p = sess_sub.add_parser("list", help="List tool sessions")
    list_p.add_argument("--json", action="store_true", help="Output JSON")

    # session show
    show_p = sess_sub.add_parser("show", help="Show session details")
    show_p.add_argument("session_id", help="Local session ID")
    show_p.add_argument("--json", action="store_true", help="Output JSON")

    # session log
    log_p = sess_sub.add_parser("log", help="View session transcript, prompts, responses, and debug logs")
    log_p.add_argument("session_id", nargs="?", default=None, help="Local session ID (default: active session)")
    log_p.add_argument("--turn", type=int, default=None, help="Filter to specific turn number")
    log_p.add_argument("--tail", type=int, default=None, help="Show last N turns")
    log_p.add_argument("--events", action="store_true", help="Show granular telemetry events instead of turn history")
    log_p.add_argument("--json", action="store_true", help="Output JSON format")

    # session set
    set_p = sess_sub.add_parser("set", help="Set active session")
    set_p.add_argument("session_id", help="Local session ID")

    # session new
    new_sess_p = sess_sub.add_parser("new", help="Create a fresh session and set as active")
    new_sess_p.add_argument("-t", "--tag", default=None, help="Tag for the new session")
    new_sess_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # session delete
    del_p = sess_sub.add_parser("delete", help="Delete a session")
    del_p.add_argument("session_id", help="Local session ID")
    del_p.add_argument("--json", action="store_true", help="Output JSON")

    # 4. steer an existing live turn
    steer_p = subparsers.add_parser("steer", help="Add an instruction to one running local session")
    steer_resume = steer_p.add_mutually_exclusive_group()
    steer_resume.add_argument("--resume", action="store_true", help="Target the active local session")
    steer_resume.add_argument("-s", "--resume-session", "--session", "--sesion", default=None, help="Target a specific running local session")
    steer_p.add_argument("-p", "--prompt", default=None, help="Steering instruction")
    steer_p.add_argument("pos_prompt", nargs="*", default=[], help="Steering instruction")
    steer_p.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    # 5. Connected ChatGPT Web MCP apps
    plugin_p = subparsers.add_parser("plugin", help="Manage connected MCP plugins")
    plugin_sub = plugin_p.add_subparsers(dest="plugin_action", required=True)
    plugin_list_p = plugin_sub.add_parser("list", help="List connected MCP plugins")
    plugin_list_p.add_argument("--json", action="store_true", help="Output JSON")
    plugin_add_p = plugin_sub.add_parser("add", help="Create, connect, and authorize a no-auth MCP plugin")
    plugin_add_p.add_argument("name", help="Name shown in ChatGPT, for example br")
    plugin_add_p.add_argument("mcp_url", help="MCP server URL")
    plugin_add_p.add_argument("--description", default="", help="Optional connector description")
    plugin_add_p.add_argument(
        "--permission", default="full_access",
        choices=("full_access", "low_risk", "read_only", "always_ask"),
        help="ChatGPT app permission after connecting (default: full_access)",
    )
    plugin_add_p.add_argument("--json", action="store_true", help="Output JSON")
    plugin_permission_p = plugin_sub.add_parser("permission", help="Change a connected plugin permission")
    plugin_permission_p.add_argument("identifier", help="Plugin name, link ID, or connector ID")
    plugin_permission_p.add_argument(
        "permission", choices=("full_access", "low_risk", "read_only", "always_ask"),
    )
    plugin_permission_p.add_argument("--json", action="store_true", help="Output JSON")
    plugin_remove_p = plugin_sub.add_parser("remove", help="Explicitly remove a plugin link and connector")
    plugin_remove_p.add_argument("identifier", help="Plugin name, link ID, or connector ID")
    plugin_remove_p.add_argument("--json", action="store_true", help="Output JSON")

    # 6. Gateway control
    start_p = subparsers.add_parser("start", help="Start gateway daemon")
    start_p.add_argument("--host", default=defaults.DEFAULT_GATEWAY_HOST)
    start_p.add_argument("--port", type=int, default=defaults.DEFAULT_GATEWAY_PORT)

    stop_p = subparsers.add_parser("stop", help="Stop gateway daemon")
    restart_p = subparsers.add_parser("restart", help="Restart gateway daemon")
    restart_p.add_argument("--host", default=defaults.DEFAULT_GATEWAY_HOST)
    restart_p.add_argument("--port", type=int, default=defaults.DEFAULT_GATEWAY_PORT)

    status_p = subparsers.add_parser("status", help="Check gateway daemon status")
    status_p.add_argument("--json", action="store_true", help="Output JSON")

    # 7. Browser login
    login_p = subparsers.add_parser("login", help="Open visible browser to log in to ChatGPT Web and refresh credentials")
    login_p.add_argument("--timeout", type=int, default=300, help="Maximum seconds to wait for login (default: 300)")
    return parser



def parse_cli_args(argv: list[str]) -> argparse.Namespace:
    """Parse commands, including the compact fresh-turn bridge shortcut.

    ``gpt -b br -p 'solve /absolute/path'`` is intentionally equivalent to
    ``gpt run -b br -p 'solve /absolute/path'``.  It always creates a fresh
    local session unless an explicit resume flag is supplied.
    """
    parser = build_cli_parser()
    if argv and argv[0].startswith("-") and argv[0] not in {"-h", "--help"}:
        return parser.parse_args(["run", *argv])
    return parser.parse_args(argv)

