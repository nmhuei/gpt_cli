from pathlib import Path

# Paths
DEFAULT_CACHE_DIR = Path.home() / ".local/share/webgpt/profiles/personal"
DEFAULT_SESSION_DIR = Path.home() / ".local/share/webgpt/sessions"
DEFAULT_LOG_DIR = Path.home() / ".local/share/webgpt/logs"
DEFAULT_PID_FILE = Path.home() / ".local/share/webgpt/gateway.pid"

# Network & Gateway
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18000
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_WORKERS = 5

# Upstream Endpoints
CHATGPT_BASE_URL = "https://chatgpt.com"
SENTINEL_PREPARE_URL = "https://chatgpt.com/backend-api/sentinel/chat-requirements/prepare"
SENTINEL_CLASSIC_URL = "https://chatgpt.com/backend-api/sentinel/chat-requirements"
FCONV_PREPARE_URL = "https://chatgpt.com/backend-api/f/conversation/prepare"
FCONV_CONVERSATION_URL = "https://chatgpt.com/backend-api/f/conversation"

# User-Agent matching impersonated Chrome/146 on Windows
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"

# Models & Preferences
DEFAULT_MODEL = "gpt-5-6-thinking"
DEFAULT_EFFORT = "extended"

# Bridge & MCP Settings
DEFAULT_BRIDGE_NAME = "brige"
DEFAULT_MCP_PERMISSION = "full_access"
DEFAULT_MCP_TUNNEL_FILE = Path("/home/light/GitHub/botquanganh_mcp/logs/tunnel_url.txt")

