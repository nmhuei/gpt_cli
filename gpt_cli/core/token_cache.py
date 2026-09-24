import json
import os
from pathlib import Path
from .types import TokenBundle
from ..config import defaults

class TokenCacheManager:
    """Manages secure 0600 disk cache for OpenAI session tokens and cookies."""
    
    def __init__(self, cache_dir: Path | str | None = None):
        self.cache_dir = Path(cache_dir or defaults.DEFAULT_CACHE_DIR).expanduser()
        self.cache_file = self.cache_dir / "webgpt-token-cache.json"

    def load_disk_cache(self) -> TokenBundle | None:
        if not self.cache_file.exists():
            return None
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            return TokenBundle(
                access_token=data.get("access_token") or data.get("accessToken") or "",
                cookies=data.get("cookies", {}),
                oai_device_id=data.get("oai_device_id") or data.get("oaiDeviceId"),
                cf_clearance=data.get("cf_clearance") or data.get("cfClearance"),
                chatgpt_account_id=data.get("chatgpt_account_id") or data.get("account_id") or data.get("accountId"),
            )
        except Exception:
            return None

    def save_disk_cache(self, bundle: TokenBundle) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "accessToken": bundle.access_token,
            "cookies": bundle.cookies,
            "oaiDeviceId": bundle.oai_device_id,
            "cfClearance": bundle.cf_clearance,
            "accountId": bundle.chatgpt_account_id,
        }
        raw = json.dumps(data, indent=2)
        # Write with 0600 permissions strictly
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(self.cache_file), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
