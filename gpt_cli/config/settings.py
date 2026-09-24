import os
from dataclasses import dataclass, field
from pathlib import Path
from . import defaults

@dataclass(slots=True)
class Settings:
    cache_dir: Path = field(default_factory=lambda: Path(os.getenv("WEBGPT_CACHE_DIR", str(defaults.DEFAULT_CACHE_DIR))))
    session_dir: Path = field(default_factory=lambda: Path(os.getenv("WEBGPT_SESSION_DIR", str(defaults.DEFAULT_SESSION_DIR))))
    log_dir: Path = field(default_factory=lambda: Path(os.getenv("WEBGPT_LOG_DIR", str(defaults.DEFAULT_LOG_DIR))))
    gateway_host: str = field(default_factory=lambda: os.getenv("WEBGPT_GATEWAY_HOST", defaults.DEFAULT_GATEWAY_HOST))
    gateway_port: int = field(default_factory=lambda: int(os.getenv("WEBGPT_GATEWAY_PORT", str(defaults.DEFAULT_GATEWAY_PORT))))
    max_workers: int = field(default_factory=lambda: int(os.getenv("WEBGPT_MAX_WORKERS", str(defaults.DEFAULT_MAX_WORKERS))))
    timeout_seconds: float = field(default_factory=lambda: float(os.getenv("WEBGPT_TIMEOUT_SECONDS", str(defaults.DEFAULT_TIMEOUT_SECONDS))))
    user_agent: str = field(default_factory=lambda: os.getenv("WEBGPT_USER_AGENT", defaults.DEFAULT_USER_AGENT))

    def ensure_directories(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
