import asyncio
import os
from pathlib import Path
from typing import Any
from ..core.types import TokenBundle
from ..core.token_cache import TokenCacheManager
from ..config import defaults

class BrowserLifebuoy:
    """Fallback lifebuoy: wakes CloakBrowser for 3-5 seconds only when token expires or 403 hits."""
    
    def __init__(self, cache_mgr: TokenCacheManager):
        self.cache_mgr = cache_mgr

    async def _extract_from_page(self, page: Any) -> dict[str, Any]:
        """Evaluates in DOM context to read session & cookies."""
        try:
            session_data = await page.evaluate("() => fetch('/api/auth/session').then(r => r.json()).catch(() => ({}))")
        except Exception:
            session_data = {}
        cookies = await page.context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        user = session_data.get("user") if isinstance(session_data, dict) else {}
        user_id = user.get("id") if isinstance(user, dict) else None
        return {
            "accessToken": session_data.get("accessToken", "") if isinstance(session_data, dict) else "",
            "cookies": cookie_dict,
            "oaiDeviceId": cookie_dict.get("oai-device-id") or cookie_dict.get("oai-did"),
            "cfClearance": cookie_dict.get("cf_clearance"),
            "accountId": session_data.get("account_id") or user_id if isinstance(session_data, dict) else None,
        }

    async def remint_credentials(self, headless: bool = True, profile_dir: Path | str | None = None) -> TokenBundle:
        """Launches stealth CloakBrowser (or fallback persistent context), extracts credentials, saves to disk, and closes browser."""
        profile_path = Path(profile_dir) if profile_dir else self.cache_mgr.cache_dir
        profile_path.mkdir(parents=True, exist_ok=True)

        for lock_file in ["SingletonLock", "SingletonSocket", "SingletonCookie"]:
            p = profile_path / lock_file
            if p.is_symlink() or p.exists():
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

        is_playwright_cm = False
        pw_cm = None
        try:
            import cloakbrowser
            context = await cloakbrowser.launch_persistent_context_async(
                str(profile_path),
                headless=headless,
                stealth_args=True,
            )
        except (ImportError, AttributeError):
            from playwright.async_api import async_playwright
            pw_cm = async_playwright()
            p = await pw_cm.__aenter__()
            is_playwright_cm = True
            context = await p.chromium.launch_persistent_context(
                str(profile_path),
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ]
            )

        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(
                defaults.CHATGPT_BASE_URL,
                wait_until="domcontentloaded",
                timeout=20000
            )
            await asyncio.sleep(2)
            data = await self._extract_from_page(page)
            cookies = data.get("cookies", {})
            token = data.get("accessToken") or ""
            if not token:
                raise RuntimeError("BrowserLifebuoy failed: Empty access token returned from browser session")
            account_id = data.get("accountId") or self.cache_mgr.extract_account_id(token)
            bundle = TokenBundle(
                access_token=token,
                cookies=cookies,
                oai_device_id=data.get("oaiDeviceId") or cookies.get("oai-device-id"),
                cf_clearance=data.get("cfClearance") or cookies.get("cf_clearance"),
                chatgpt_account_id=account_id
            )
            self.cache_mgr.save_disk_cache(bundle)
            return bundle
        finally:
            await context.close()
            if is_playwright_cm and pw_cm is not None:
                try:
                    await pw_cm.__aexit__(None, None, None)
                except Exception:
                    pass
