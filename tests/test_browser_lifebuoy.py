import asyncio
import base64
import json
import pytest
import time
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
from gpt_cli.core.token_cache import TokenCacheManager
from gpt_cli.transport.lifebuoy import BrowserLifebuoy
from gpt_cli.core.types import TokenBundle


def _jwt_with_expiration(expiration: int) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps({"exp": expiration}).encode()
    ).decode().rstrip("=")
    return f"header.{payload}.signature"

def test_lifebuoy_triggers_refresh(tmp_path: Path):
    async def _run():
        cache_mgr = TokenCacheManager(tmp_path)
        lifebuoy = BrowserLifebuoy(cache_mgr)

        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_context.pages = [mock_page]

        mock_cloak = MagicMock()
        mock_cloak.launch_persistent_context_async = AsyncMock(return_value=mock_context)

        with patch.dict("sys.modules", {"cloakbrowser": mock_cloak}):
            with patch.object(lifebuoy, "_extract_from_page", new_callable=AsyncMock) as mock_extract:
                mock_extract.return_value = {
                    "accessToken": "new_refreshed_token_xyz",
                    "cookies": {"cf_clearance": "new_clearance_123"},
                    "oaiDeviceId": "new_dev_uuid",
                    "accountId": "acc_001",
                }
                bundle = await lifebuoy.remint_credentials(headless=True)
                assert bundle.access_token == "new_refreshed_token_xyz"
                assert bundle.cf_clearance == "new_clearance_123"
                assert bundle.oai_device_id == "new_dev_uuid"

                # Verify saved to disk
                loaded = cache_mgr.load_disk_cache()
                assert loaded is not None
                assert loaded.access_token == "new_refreshed_token_xyz"
                assert loaded.cf_clearance == "new_clearance_123"

    asyncio.run(_run())


def test_lifebuoy_leaves_browser_lock_files_untouched(tmp_path: Path):
    lock_file = tmp_path / "SingletonLock"
    lock_file.write_text("owned-by-running-browser", encoding="utf-8")

    async def _run():
        manager = TokenCacheManager(tmp_path)
        lifebuoy = BrowserLifebuoy(manager)
        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_context.pages = [mock_page]
        mock_cloak = MagicMock()
        mock_cloak.launch_persistent_context_async = AsyncMock(return_value=mock_context)
        with patch.dict("sys.modules", {"cloakbrowser": mock_cloak}):
            with patch.object(lifebuoy, "_extract_from_page", new_callable=AsyncMock) as extract:
                extract.return_value = {"accessToken": "fresh_token", "cookies": {}}
                with pytest.raises(RuntimeError, match="profile is already in use"):
                    await lifebuoy.remint_credentials(headless=True)
        mock_cloak.launch_persistent_context_async.assert_not_awaited()

    asyncio.run(_run())
    assert lock_file.read_text(encoding="utf-8") == "owned-by-running-browser"


def test_lifebuoy_rejects_expired_token_without_overwriting_cache(tmp_path: Path):
    async def _run():
        cache_mgr = TokenCacheManager(tmp_path)
        original = TokenBundle(access_token="old_cached_token")
        cache_mgr.save_disk_cache(original)
        lifebuoy = BrowserLifebuoy(cache_mgr)

        mock_context = AsyncMock()
        mock_page = AsyncMock()
        mock_context.pages = [mock_page]
        mock_cloak = MagicMock()
        mock_cloak.launch_persistent_context_async = AsyncMock(return_value=mock_context)

        with patch.dict("sys.modules", {"cloakbrowser": mock_cloak}):
            with patch.object(lifebuoy, "_extract_from_page", new_callable=AsyncMock) as extract:
                extract.return_value = {
                    "accessToken": _jwt_with_expiration(int(time.time()) - 3600),
                    "cookies": {},
                }
                with pytest.raises(RuntimeError, match="returned an expired access token"):
                    await lifebuoy.remint_credentials(headless=True)

        assert cache_mgr.load_disk_cache().access_token == "old_cached_token"

    asyncio.run(_run())
