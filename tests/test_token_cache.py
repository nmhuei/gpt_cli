import json
import pytest
from pathlib import Path
from gpt_cli.core.types import TokenBundle
from gpt_cli.core.token_cache import TokenCacheManager

def test_token_bundle_roundtrip(tmp_path: Path):
    cache_file = tmp_path / "webgpt-token-cache.json"
    bundle = TokenBundle(
        access_token="test_token_12345",
        cookies={"cf_clearance": "cf_test", "__Secure-next-auth.session-token": "sess_test"},
        oai_device_id="dev_uuid_123",
        cf_clearance="cf_test"
    )
    mgr = TokenCacheManager(cache_dir=tmp_path)
    mgr.save_disk_cache(bundle)
    
    assert cache_file.exists()
    assert (cache_file.stat().st_mode & 0o777) == 0o600
    
    loaded = mgr.load_disk_cache()
    assert loaded is not None
    assert loaded.access_token == "test_token_12345"
    assert loaded.oai_device_id == "dev_uuid_123"
    assert loaded.cf_clearance == "cf_test"
    assert loaded.cookies["cf_clearance"] == "cf_test"

def test_token_cache_missing_returns_none(tmp_path: Path):
    mgr = TokenCacheManager(cache_dir=tmp_path / "nonexistent")
    assert mgr.load_disk_cache() is None
