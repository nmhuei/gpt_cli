import base64
import json
import os
import time
import uuid
from pathlib import Path
from .types import TokenBundle
from ..config import defaults

class TokenCacheManager:
    """Manages secure 0600 disk cache for OpenAI session tokens and cookies."""
    
    def __init__(self, cache_dir: Path | str | None = None):
        self.cache_dir = Path(cache_dir or defaults.DEFAULT_CACHE_DIR).expanduser()
        self.cache_file = self.cache_dir / "webgpt-token-cache.json"

    def is_token_expired(self, token: str, skew_seconds: int = 60) -> bool:
        """Check if JWT access token is expired based on its 'exp' claim."""
        if not token or not isinstance(token, str):
            return True
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return False
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            exp = payload.get("exp")
            if exp and isinstance(exp, (int, float)):
                return time.time() >= (exp - skew_seconds)
        except Exception:
            pass
        return False

    def extract_account_id(self, token: str) -> str | None:
        """Extract chatgpt_account_id claim from JWT token payload."""
        if not token or not isinstance(token, str):
            return None
        try:
            parts = token.split(".")
            if len(parts) >= 2:
                padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
                auth_claim = payload.get("https://api.openai.com/auth")
                if isinstance(auth_claim, dict) and auth_claim.get("chatgpt_account_id"):
                    return auth_claim["chatgpt_account_id"]
                return payload.get("chatgpt_account_id")
        except Exception:
            pass
        return None

    def load_disk_cache(self) -> TokenBundle | None:
        if not self.cache_file.exists():
            return None
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            token = data.get("access_token") or data.get("accessToken") or ""
            cookies = data.get("cookies", {})
            if not token and not cookies:
                return None
            account_id = data.get("chatgpt_account_id") or data.get("account_id") or data.get("accountId")
            if not account_id and token:
                account_id = self.extract_account_id(token)
            return TokenBundle(
                access_token=token,
                cookies=cookies,
                oai_device_id=data.get("oai_device_id") or data.get("oaiDeviceId"),
                cf_clearance=data.get("cf_clearance") or data.get("cfClearance"),
                chatgpt_account_id=account_id,
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
        # Atomic write with strict 0600 permissions
        tmp_file = self.cache_dir / f".tmp_{uuid.uuid4().hex}"
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(tmp_file), flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        try:
            os.chmod(str(tmp_file), 0o600)
            os.replace(str(tmp_file), str(self.cache_file))
            os.chmod(str(self.cache_file), 0o600)
        finally:
            if tmp_file.exists():
                try:
                    tmp_file.unlink(missing_ok=True)
                except Exception:
                    pass
