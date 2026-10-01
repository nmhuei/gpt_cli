import pytest
from unittest.mock import MagicMock, patch
from gpt_cli.plugins.manager import PluginManager, PluginInfo
from gpt_cli.core.types import TokenBundle

@pytest.fixture
def mock_cache_mgr():
    mgr = MagicMock()
    mgr.load_disk_cache.return_value = TokenBundle(
        access_token="test_token",
        cookies={"cf_clearance": "dummy"},
        chatgpt_account_id="acc_123"
    )
    return mgr

def test_list_plugins(mock_cache_mgr):
    with patch("gpt_cli.plugins.manager.requests.Session") as mock_session_cls:
        session_instance = MagicMock()
        mock_session_cls.return_value = session_instance
        session_instance.post.return_value.status_code = 200
        session_instance.post.return_value.json.return_value = {
            "links": [
                {
                    "id": "link_123",
                    "connector_id": "conn_123",
                    "name": "bqa",
                    "apps_privacy_control": "full_access",
                    "actions": ["health_check"],
                },
                {
                    "id": "implicit_link::pdf",
                    "name": "PDF",
                }
            ]
        }

        mgr = PluginManager(cache_mgr=mock_cache_mgr)
        plugins = mgr.list_plugins(include_system=False)
        assert len(plugins) == 1
        assert plugins[0].name == "bqa"
        assert plugins[0].apps_privacy_control == "full_access"

        all_plugins = mgr.list_plugins(include_system=True)
        assert len(all_plugins) == 2


def test_get_connector_source_returns_native_connector_metadata(mock_cache_mgr):
    with patch("gpt_cli.plugins.manager.requests.Session") as mock_session_cls:
        session_instance = MagicMock()
        mock_session_cls.return_value = session_instance
        session_instance.get.return_value.status_code = 200
        session_instance.get.return_value.json.return_value = {
            "id": "conn_123", "name": "bqa", "actions": [{"name": "host_workspace_bind"}],
        }

        source = PluginManager(cache_mgr=mock_cache_mgr).get_connector_source("conn_123")

        assert source["id"] == "conn_123"
        assert source["actions"] == [{"name": "host_workspace_bind"}]

def test_add_mcp_plugin_flow(mock_cache_mgr):
    with patch("gpt_cli.plugins.manager.requests.Session") as mock_session_cls:
        session_instance = MagicMock()
        mock_session_cls.return_value = session_instance

        mcp_resp = MagicMock(status_code=200)
        mcp_resp.json.return_value = {"connector": {"id": "asdk_app_test", "name": "bqa"}}

        link_resp = MagicMock(status_code=200)
        link_resp.json.return_value = {"id": "link_test", "connector_id": "asdk_app_test", "actions": ["health_check"]}

        patch_resp = MagicMock(status_code=200)
        patch_resp.json.return_value = {"link": {"id": "link_test", "apps_privacy_control": "full_access"}}

        list_resp = MagicMock(status_code=200)
        list_resp.json.return_value = {
            "links": [
                {
                    "id": "link_test",
                    "connector_id": "asdk_app_test",
                    "name": "bqa",
                    "apps_privacy_control": "full_access",
                    "actions": ["health_check"]
                }
            ]
        }

        def mock_post(url, *args, **kwargs):
            if "/mcp" in url:
                return mcp_resp
            elif "/noauth" in url:
                return link_resp
            return list_resp

        session_instance.post.side_effect = mock_post
        session_instance.patch.return_value = patch_resp

        mgr = PluginManager(cache_mgr=mock_cache_mgr)
        result = mgr.add_mcp_plugin("bqa", "https://example.com/mcp", permission="full_access")
        assert result.link_id == "link_test"
        assert result.apps_privacy_control == "full_access"

def test_add_mcp_plugin_upfront_permission_validation(mock_cache_mgr):
    mgr = PluginManager(cache_mgr=mock_cache_mgr)
    with pytest.raises(ValueError) as exc:
        mgr.add_mcp_plugin("bad", "https://example.com/mcp", permission="invalid_perm_level")
    assert "Invalid permission" in str(exc.value)

def test_delete_plugin_partial_failure(mock_cache_mgr):
    with patch("gpt_cli.plugins.manager.requests.Session") as mock_session_cls:
        session_instance = MagicMock()
        mock_session_cls.return_value = session_instance

        # Mock list to find plugin
        session_instance.post.return_value.status_code = 200
        session_instance.post.return_value.json.return_value = {
            "links": [{"id": "link_123", "connector_id": "conn_123", "name": "bqa"}]
        }

        # Mock delete link succeeds (204), but delete connector fails (500)
        def mock_delete(url, *args, **kwargs):
            resp = MagicMock()
            if "/links/" in url:
                resp.status_code = 204
            else:
                resp.status_code = 500
            return resp

        session_instance.delete.side_effect = mock_delete

        mgr = PluginManager(cache_mgr=mock_cache_mgr)
        res = mgr.delete_plugin("bqa")
        assert res.link_deleted is True
        assert res.connector_deleted is False
        assert res.success is False
        assert "Failed to delete connector" in str(res.error)

def test_delete_plugin_full_success(mock_cache_mgr):
    with patch("gpt_cli.plugins.manager.requests.Session") as mock_session_cls:
        session_instance = MagicMock()
        mock_session_cls.return_value = session_instance

        session_instance.post.return_value.status_code = 200
        session_instance.post.return_value.json.return_value = {
            "links": [{"id": "link_123", "connector_id": "conn_123", "name": "bqa"}]
        }

        session_instance.delete.return_value.status_code = 204

        mgr = PluginManager(cache_mgr=mock_cache_mgr)
        res = mgr.delete_plugin("bqa")
        assert res.link_deleted is True
        assert res.connector_deleted is True
        assert res.success is True
        assert bool(res) is True
