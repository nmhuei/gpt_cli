import os
from pathlib import Path


def user_data_dir() -> Path:
    """Return the per-user data root while preserving the established state path."""
    root = os.getenv("XDG_DATA_HOME") or os.getenv("LOCALAPPDATA")
    if root:
        return Path(root).expanduser() / "webgpt"
    return Path.home() / ".local" / "share" / "webgpt"


def user_config_dir() -> Path:
    """Return the per-user config root using the platform's configured location."""
    root = os.getenv("XDG_CONFIG_HOME") or os.getenv("APPDATA")
    if root:
        return Path(root).expanduser() / "gpt_cli"
    return Path.home() / ".config" / "gpt_cli"


def user_runtime_dir() -> Path:
    """Return the private runtime root used by short-lived local control sockets."""
    root = os.getenv("XDG_RUNTIME_DIR")
    if root:
        return Path(root).expanduser() / "webgpt"
    return user_data_dir() / "runtime"


def default_mcp_tunnel_file() -> Path:
    return user_config_dir() / "mcp" / "tunnel_url.txt"

# Paths
DEFAULT_CACHE_DIR = user_data_dir() / "profiles" / "personal"
DEFAULT_SESSION_DIR = user_data_dir() / "sessions"
DEFAULT_LOG_DIR = user_data_dir() / "logs"
DEFAULT_PID_FILE = user_data_dir() / "gateway.pid"

# Network & Gateway
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18000
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_WORKERS = 3
DEFAULT_BQA_ACTIVITY_URL = "http://127.0.0.1:18427/api/v1/activity/stream"

# Upstream Endpoints
CHATGPT_BASE_URL = "https://chatgpt.com"
SENTINEL_PREPARE_URL = "https://chatgpt.com/backend-api/sentinel/chat-requirements/prepare"
SENTINEL_FINALIZE_URL = "https://chatgpt.com/backend-api/sentinel/chat-requirements/finalize"
SENTINEL_CLASSIC_URL = "https://chatgpt.com/backend-api/sentinel/chat-requirements"
FCONV_PREPARE_URL = "https://chatgpt.com/backend-api/f/conversation/prepare"
FCONV_CONVERSATION_URL = "https://chatgpt.com/backend-api/f/conversation"
FCONV_STEER_URL = "https://chatgpt.com/backend-api/f/steer_turn"

DEFAULT_BUILD_HASH = "d92c92cf3397a05dfc98298acaae4f90b44e8386"

# User-Agent matching impersonated Chrome/146 on Windows
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"

# Models & Preferences
DEFAULT_MODEL = "gpt-5-6-thinking"
DEFAULT_EFFORT = "extended"

# Bridge & MCP Settings
DEFAULT_BRIDGE_NAME: str | None = None
DEFAULT_MCP_PERMISSION = "read_only"
DEFAULT_MCP_TUNNEL_FILE = default_mcp_tunnel_file()

# Server-Side DAG Recovery Settings
DEFAULT_RECOVERY_POLL_ATTEMPTS = 10
DEFAULT_RECOVERY_POLL_INTERVAL = 2.0
