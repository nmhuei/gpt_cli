import pytest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from gpt_cli.core.token_cache import TokenCacheManager
from gpt_cli.transport.lifebuoy import BrowserLifebuoy

@pytest.mark.asyncio
async def test_lifebuoy_triggers_refresh(tmp_path: Path):
    cache_mgr = TokenCacheManager(tmp_path)
    lifebuoy = BrowserLifebuoy(cache_mgr)

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
