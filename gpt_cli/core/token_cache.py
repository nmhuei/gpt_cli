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
        target = cache_dir or os.getenv("WEBGPT_CACHE_DIR") or defaults.DEFAULT_CACHE_DIR
        self.cache_dir = Path(target).expanduser()
        self.cache_file = self.cache_dir / "webgpt-token-cache.json"

    def is_token_expired(self, token: str, skew_seconds: int = 60) -> bool:
        """Check if JWT access token is expired based on its 'exp' claim."""
        if not token or not isinstance(token, str):
            return True
        try:
            parts = token.split(".")
            if len(parts) < 2:
                return True
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
            if not isinstance(payload, dict):
                return True
            exp = payload.get("exp")
            if exp and isinstance(exp, (int, float)):
                return time.time() >= (exp - skew_seconds)
            return True
        except Exception:
            return True

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
                acc = None
                if isinstance(auth_claim, dict) and auth_claim.get("chatgpt_account_id"):
                    acc = auth_claim["chatgpt_account_id"]
                if not acc:
                    acc = payload.get("chatgpt_account_id")
                if acc and isinstance(acc, str) and not acc.startswith("user-"):
                    return acc
        except Exception:
            pass
        return None

    def load_disk_cache(self) -> TokenBundle | None:
        if not self.cache_file.exists():
            return None
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            token = data.get("access_token") or data.get("accessToken") or ""
            raw_cookies = data.get("cookies")
            cookies: dict[str, str] = raw_cookies if isinstance(raw_cookies, dict) else {}
            if not token and not cookies:
                return None
            raw_acc = data.get("chatgpt_account_id") or data.get("account_id") or data.get("accountId")
            account_id = (
                raw_acc
                if isinstance(raw_acc, str) and not raw_acc.startswith("user-")
                else None
            )
            if not account_id and token:
                account_id = self.extract_account_id(token)
            oai_device_id = (
                data.get("oai_device_id")
                or data.get("oaiDeviceId")
                or cookies.get("oai-device-id")
                or cookies.get("oai-did")
            )
            cf_clearance = (
                data.get("cf_clearance")
                or data.get("cfClearance")
                or cookies.get("cf_clearance")
            )
            return TokenBundle(
                access_token=token,
                cookies=cookies,
                oai_device_id=oai_device_id,
                cf_clearance=cf_clearance,
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
