import os
from dataclasses import dataclass, field
from pathlib import Path
from . import defaults

def load_dotenv_search() -> Path | None:
    """Finds and loads .env from candidate paths."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path.home() / ".local/share/webgpt/.env",
        Path.home() / ".config/webgpt/.env",
    ]
    for p in candidates:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
                return p
            except Exception:
                pass
    return None

# Auto-load on import
_loaded_env_path = load_dotenv_search()

@dataclass(slots=True)
class Settings:
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("WEBGPT_CACHE_DIR", str(defaults.user_data_dir() / "profiles" / "personal"))).expanduser())
    session_dir: Path = field(default_factory=lambda: Path(os.getenv("WEBGPT_SESSION_DIR", str(defaults.user_data_dir() / "sessions"))).expanduser())
    log_dir: Path = field(default_factory=lambda: Path(os.getenv("WEBGPT_LOG_DIR", str(defaults.user_data_dir() / "logs"))).expanduser())
    gateway_host: str = field(default_factory=lambda: os.getenv("WEBGPT_GATEWAY_HOST", defaults.DEFAULT_GATEWAY_HOST))
    gateway_port: int = field(default_factory=lambda: int(os.getenv("WEBGPT_GATEWAY_PORT", str(defaults.DEFAULT_GATEWAY_PORT))))
    max_workers: int = field(default_factory=lambda: int(os.getenv("WEBGPT_MAX_WORKERS", str(defaults.DEFAULT_MAX_WORKERS))))
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("WEBGPT_TIMEOUT_SECONDS", str(defaults.DEFAULT_TIMEOUT_SECONDS))))
    user_agent: str = field(default_factory=lambda: os.getenv("WEBGPT_USER_AGENT", defaults.DEFAULT_USER_AGENT))

    # Model & Thinking defaults
    default_model: str = field(default_factory=lambda: os.getenv("DEFAULT_MODEL", defaults.DEFAULT_MODEL))
    default_effort: str = field(default_factory=lambda: os.getenv("DEFAULT_EFFORT", defaults.DEFAULT_EFFORT))

    # Bridge & MCP defaults
    default_bridge_name: str | None = field(default_factory=lambda: os.getenv("DEFAULT_BRIDGE_NAME") or defaults.DEFAULT_BRIDGE_NAME)
    default_mcp_permission: str = field(default_factory=lambda: os.getenv("DEFAULT_MCP_PERMISSION", defaults.DEFAULT_MCP_PERMISSION))
    default_mcp_url: str | None = field(default_factory=lambda: os.getenv("DEFAULT_MCP_URL"))
    default_mcp_tunnel_file: Path = field(
        default_factory=lambda: Path(os.getenv("DEFAULT_MCP_TUNNEL_FILE", str(defaults.default_mcp_tunnel_file()))).expanduser()
    )
    # BQA is a local, redacted host-activity side channel.  It is deliberately
    # independent of the public MCP tunnel and only used for bound sessions.
    bqa_activity_url: str = field(
        default_factory=lambda: os.getenv(
            "BQA_ACTIVITY_URL", "http://127.0.0.1:18427/api/v1/activity/stream"
        ).rstrip("/")
    )

    def resolve_mcp_url(self, explicit_url: str | None = None) -> str:
        """Resolves target MCP URL: explicit -> env static -> dynamic tunnel file."""
        if explicit_url:
            return explicit_url.strip()
        if self.default_mcp_url:
            return self.default_mcp_url.strip()
        if self.default_mcp_tunnel_file.is_file():
            try:
                base = self.default_mcp_tunnel_file.read_text(encoding="utf-8").strip()
                if base:
                    if not base.endswith("/mcp"):
                        base = base.rstrip("/") + "/mcp"
                    return base
            except Exception:
                pass
        raise ValueError(
            "No MCP URL provided and could not resolve from DEFAULT_MCP_URL or DEFAULT_MCP_TUNNEL_FILE. "
            "Please specify a URL or ensure the tunnel is running."
        )

    def ensure_directories(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
