import asyncio
from pathlib import Path
from typing import Any
from ..core.types import TokenBundle
from ..core.token_cache import TokenCacheManager
from ..config import defaults

class BrowserLifebuoy:
    """Fallback lifebuoy: wakes Chromium for 3-5 seconds only when token expires or 403 hits."""
    
    def __init__(self, cache_mgr: TokenCacheManager):
        self.cache_mgr = cache_mgr

    async def _extract_from_page(self, page: Any) -> dict[str, Any]:
        """Evaluates in DOM context to read session & cookies."""
        session_data = await page.evaluate("() => fetch('/api/auth/session').then(r => r.json())")
        cookies = await page.context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        return {
            "accessToken": session_data.get("accessToken", ""),
            "cookies": cookie_dict,
            "oaiDeviceId": cookie_dict.get("oai-device-id"),
            "cfClearance": cookie_dict.get("cf_clearance"),
            "accountId": session_data.get("user", {}).get("id")
        }

    async def remint_credentials(self, headless: bool = True, profile_dir: Path | str | None = None) -> TokenBundle:
        """Launches lightweight Chromium, extracts credentials, saves to disk, and closes browser."""
        from playwright.async_api import async_playwright
        profile_path = Path(profile_dir) if profile_dir else (self.cache_mgr.cache_dir / "cloak-profile")
        profile_path.mkdir(parents=True, exist_ok=True)
        
        async with async_playwright() as p:
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
                    timeout=15000
                )
                data = await self._extract_from_page(page)
                cookies = data.get("cookies", {})
                token = data.get("accessToken") or ""
                if not token:
                    raise RuntimeError("BrowserLifebuoy failed: Empty access token returned from browser session")
                bundle = TokenBundle(
                    access_token=token,
                    cookies=cookies,
                    oai_device_id=data.get("oaiDeviceId") or cookies.get("oai-device-id"),
                    cf_clearance=data.get("cfClearance") or cookies.get("cf_clearance"),
                    chatgpt_account_id=data.get("accountId")
                )
                self.cache_mgr.save_disk_cache(bundle)
                return bundle
            finally:
                await context.close()
