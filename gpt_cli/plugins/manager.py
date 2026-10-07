import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from curl_cffi import requests

from ..config import defaults
from ..core.token_cache import TokenCacheManager

logger = logging.getLogger("gpt_cli.plugins")

VALID_PERMISSIONS = {
    "full_access": "Allow all tools (ELEVATED RISK)",
    "low_risk": "Allow low-risk tools (DEFAULT)",
    "read_only": "Allow read tools",
    "always_ask": "Always ask",
}

@dataclass
class PluginInfo:
    link_id: str
    connector_id: str
    name: str
    description: str = ""
    apps_privacy_control: str = "full_access"
    actions: list[str] = field(default_factory=list)
    auth_type: str = "NONE"
    auth_status: str = "ACTIVE"
    created_at: str = ""
    is_custom: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def permission_display(self) -> str:
        return VALID_PERMISSIONS.get(self.apps_privacy_control, self.apps_privacy_control or "None")

@dataclass
class DeleteResult:
    identifier: str
    link_deleted: bool = False
    connector_deleted: bool = False
    success: bool = False
    error: str | None = None

    def __bool__(self) -> bool:
        return self.success


class PluginManager:
    """Manages ChatGPT Web ASDK / MCP Connectors and tool permissions."""

    def __init__(self, cache_mgr: TokenCacheManager | None = None):
        self.cache_mgr = cache_mgr or TokenCacheManager()
        self.bundle = self.cache_mgr.load_disk_cache()
        if not self.bundle or not self.bundle.access_token:
            raise RuntimeError(
                "No valid token bundle found in cache (~/.local/share/webgpt/profiles/personal). "
                "Please run interactive browser login or refresh credentials."
            )
        self.base_url = defaults.CHATGPT_BASE_URL
        if self.cache_mgr.is_token_expired(self.bundle.access_token):
            self.bundle = self._refresh_expired_bundle()

        self._session: requests.Session | None = None
        self._configure_auth(self.bundle)

    def _refresh_expired_bundle(self):
        """Refresh an expired cached token from the authenticated browser profile."""
        try:
            from ..transport.lifebuoy import BrowserLifebuoy

            bundle = asyncio.run(
                BrowserLifebuoy(self.cache_mgr).remint_credentials(headless=True)
            )
        except Exception as exc:
            logger.warning(
                "Cached ChatGPT token expired; browser refresh failed (%s)",
                type(exc).__name__,
            )
            detail = str(exc)
            if detail.startswith("GPT-CLI browser profile is already in use"):
                raise RuntimeError(
                    "The cached ChatGPT access token has expired. "
                    f"{detail} No browser lock files were removed."
                ) from None
            raise RuntimeError(
                "The cached ChatGPT access token has expired and automatic refresh "
                "failed. Sign in again with the GPT-CLI browser profile, then retry."
            ) from None

        if not bundle or not bundle.access_token or self.cache_mgr.is_token_expired(bundle.access_token):
            raise RuntimeError(
                "The cached ChatGPT access token has expired, but the browser profile "
                "did not provide a fresh token. Sign in again with the GPT-CLI browser profile."
            )
        return bundle

    def _configure_auth(self, bundle) -> None:
        self.bundle = bundle
        self.headers = {
            "Authorization": f"Bearer {bundle.access_token}",
            "OAI-Product-Sku": "CONNECTOR_SETTING",
            "Content-Type": "application/json",
            "User-Agent": defaults.DEFAULT_USER_AGENT,
            "Referer": f"{self.base_url}/",
            "Origin": self.base_url,
        }
        if bundle.chatgpt_account_id:
            self.headers["ChatGPT-Account-ID"] = bundle.chatgpt_account_id

    def _request(self, method: str, url: str, **kwargs):
        """Issue one request, retrying once if another process rotated the cache token."""
        kwargs["headers"] = self.headers
        response = getattr(self.session, method)(url, **kwargs)
        if response.status_code != 401:
            return response

        cached_bundle = self.cache_mgr.load_disk_cache()
        if (
            not cached_bundle
            or not cached_bundle.access_token
            or cached_bundle.access_token == self.bundle.access_token
            or self.cache_mgr.is_token_expired(cached_bundle.access_token)
        ):
            return response

        try:
            if self._session is not None:
                self._session.close()
        finally:
            self._session = None
        self._configure_auth(cached_bundle)
        kwargs["headers"] = self.headers
        return getattr(self.session, method)(url, **kwargs)

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session(impersonate="chrome146")
            if self.bundle.cookies:
                self._session.cookies.update(self.bundle.cookies)
        return self._session

    def list_plugins(self, include_system: bool = False) -> list[PluginInfo]:
        """Lists all registered MCP connectors and links."""
        url = f"{self.base_url}/backend-api/aip/connectors/links/list_accessible"
        payload = {"principals": [], "link_refresh_strategy": "NONE"}
        
        resp = self._request("post", url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"Failed to list connectors: HTTP {resp.status_code} - {resp.text}")

        data = resp.json()
        raw_links = data.get("links", [])
        plugins: list[PluginInfo] = []

        for item in raw_links:
            link_id = item.get("id", "")
            is_custom = not link_id.startswith("implicit_link::")
            if not include_system and not is_custom:
                continue

            connector = item.get("connector", {})
            conn_id = item.get("connector_id") or connector.get("id") or ""
            name = item.get("name") or connector.get("name") or "Unnamed"
            desc = connector.get("description") or ""
            privacy = item.get("apps_privacy_control") or "None"
            actions = item.get("actions") or []
            auth_type = item.get("auth_type") or "NONE"
            auth_status = item.get("auth_status") or "UNKNOWN"
            created_at = item.get("created_at") or ""

            plugins.append(
                PluginInfo(
                    link_id=link_id,
                    connector_id=conn_id,
                    name=name,
                    description=desc,
                    apps_privacy_control=privacy,
                    actions=actions,
                    auth_type=auth_type,
                    auth_status=auth_status,
                    created_at=created_at,
                    is_custom=is_custom,
                    raw=item,
                )
            )

        return plugins

    def find_plugin(self, identifier: str) -> PluginInfo | None:
        """Finds a plugin by link_id, connector_id, or name (case-insensitive)."""
        plugins = self.list_plugins(include_system=True)
        ident_lower = identifier.lower().strip()
        for p in plugins:
            if (
                p.link_id.lower() == ident_lower
                or p.connector_id.lower() == ident_lower
                or p.name.lower() == ident_lower
            ):
                return p
        return None

    def get_connector_source(self, connector_id: str) -> dict[str, Any]:
        """Fetch the native connector object used in message source metadata."""
        url = f"{self.base_url}/backend-api/aip/connectors/{connector_id}"
        response = self._request("get", url)
        if response.status_code != 200:
            raise RuntimeError(f"Failed to fetch connector source: HTTP {response.status_code}")
        source = response.json()
        if not isinstance(source, dict) or source.get("id") != connector_id:
            raise RuntimeError("Connector source response did not match the requested connector")
        return source

    def add_mcp_plugin(
        self,
        name: str,
        mcp_url: str,
        description: str = "",
        permission: str = "full_access",
    ) -> PluginInfo:
        """Registers a new MCP connector, links it, and configures permissions (default: full_access)."""
        if permission and permission not in VALID_PERMISSIONS:
            raise ValueError(
                f"Invalid permission '{permission}'. Must be one of: {list(VALID_PERMISSIONS.keys())}"
            )

        clean_url = mcp_url.strip()
        if clean_url.endswith("/"):
            clean_url = clean_url.rstrip("/")
        
        # Helper to attempt connector creation
        def try_create_connector(target_url: str) -> tuple[int, dict[str, Any]]:
            endpoint = f"{self.base_url}/backend-api/aip/connectors/mcp"
            body = {
                "name": name,
                "description": description or f"MCP Server {name}",
                "mcp_url": target_url,
                "auth_request": {"supported_auth": [{"type": "NONE"}]},
            }
            res = self._request("post", endpoint, json=body)
            try:
                data = res.json()
            except Exception:
                data = {"raw_text": res.text}
            return res.status_code, data

        # Step 1: Create MCP connector.  Existing plugins are never removed:
        # when the name is taken, append a numeric suffix (br -> br-2 -> br-3).
        try:
            taken = {p.name.lower() for p in self.list_plugins(include_system=True)}
        except RuntimeError:
            taken = set()
        base_name = name
        suffix = 2
        while name.lower() in taken:
            name = f"{base_name}-{suffix}"
            suffix += 1
        status_code, data = try_create_connector(clean_url)
        # If failed with 424 and no path, attempt auto-appending /mcp
        if status_code in (404, 424) and not clean_url.endswith("/mcp"):
            alt_url = clean_url + "/mcp"
            alt_status, alt_data = try_create_connector(alt_url)
            if alt_status == 200:
                clean_url = alt_url
                status_code = alt_status
                data = alt_data

        # A duplicate name belongs to an existing user app.  Never delete it as
        # a side effect of an add request: removal is an explicit CLI command.
        if status_code == 409:
            existing_id = data.get("detail", {}).get("existing_connector_id")
            if existing_id:
                raise RuntimeError(
                    f"MCP connector '{name}' already exists ({existing_id}). "
                    "Choose another name or remove it explicitly first."
                )

        if status_code != 200:
            raise RuntimeError(
                f"Failed to create MCP connector '{name}' at {clean_url}: "
                f"HTTP {status_code} - {json.dumps(data)}"
            )

        conn_obj = data.get("connector", {})
        connector_id = conn_obj.get("id") or data.get("id")
        if not connector_id:
            raise RuntimeError(f"Unexpected response when creating connector: {json.dumps(data)}")

        # Step 2: Create link for no-auth connector
        link_url = f"{self.base_url}/backend-api/aip/connectors/links/noauth"
        link_body = {
            "connector_id": connector_id,
            "name": name,
            "action_names": [],
            "link_params": None,
            "action_param_schemas": None,
        }
        link_resp = self._request("post", link_url, json=link_body)
        if link_resp.status_code != 200:
            # Clean up the dangling connector
            self._request("delete", f"{self.base_url}/backend-api/aip/connectors/{connector_id}")
            raise RuntimeError(
                f"Failed to link connector '{name}': HTTP {link_resp.status_code} - {link_resp.text}"
            )

        link_data = link_resp.json()
        link_obj = link_data.get("link") if "link" in link_data else link_data
        link_id = link_obj.get("id")
        if not link_id:
            raise RuntimeError(f"Unexpected link response: {link_data}")

        # Step 3: Configure permission (default: full_access = Allow all tools / ELEVATED RISK)
        if permission:
            self.set_permission(link_id, permission=permission)

        # Retrieve and return fresh PluginInfo
        updated = self.find_plugin(link_id)
        if updated:
            return updated

        return PluginInfo(
            link_id=link_id,
            connector_id=connector_id,
            name=name,
            description=description,
            apps_privacy_control=permission,
            actions=link_obj.get("actions", []),
        )

    def set_permission(self, identifier: str, permission: str = "full_access") -> PluginInfo:
        """Configures privacy control level for a link ('full_access', 'low_risk', 'read_only', 'always_ask')."""
        if permission not in VALID_PERMISSIONS:
            raise ValueError(
                f"Invalid permission '{permission}'. Must be one of: {list(VALID_PERMISSIONS.keys())}"
            )

        plugin = self.find_plugin(identifier)
        link_id = plugin.link_id if plugin else identifier

        url = f"{self.base_url}/backend-api/aip/connectors/links/{link_id}"
        payload = {"apps_privacy_control": permission}
        resp = self._request("patch", url, json=payload)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Failed to set permission for link '{link_id}': HTTP {resp.status_code} - {resp.text}"
            )

        updated = self.find_plugin(link_id)
        if updated:
            return updated
        return PluginInfo(link_id=link_id, connector_id="", name=identifier, apps_privacy_control=permission)

    def delete_plugin(self, identifier: str) -> DeleteResult:
        """Deletes both the link and connector for the given name or ID."""
        plugin = self.find_plugin(identifier)
        link_id = plugin.link_id if plugin else (identifier if identifier.startswith("link_") else "")
        conn_id = plugin.connector_id if plugin else (identifier if identifier.startswith("asdk_") else "")

        link_deleted = False
        conn_deleted = False
        err_msg = None

        # Delete link
        if link_id:
            url_link = f"{self.base_url}/backend-api/aip/connectors/links/{link_id}"
            r_link = self._request("delete", url_link)
            if r_link.status_code in (200, 204, 404):
                link_deleted = True
            else:
                err_msg = f"Failed to delete link {link_id}: HTTP {r_link.status_code}"

        # Delete connector
        if conn_id:
            url_conn = f"{self.base_url}/backend-api/aip/connectors/{conn_id}"
            r_conn = self._request("delete", url_conn)
            if r_conn.status_code in (200, 204, 404):
                conn_deleted = True
            else:
                c_err = f"Failed to delete connector {conn_id}: HTTP {r_conn.status_code}"
                err_msg = f"{err_msg}; {c_err}" if err_msg else c_err

        if not link_id and not conn_id:
            url_link = f"{self.base_url}/backend-api/aip/connectors/links/{identifier}"
            r_link = self._request("delete", url_link)
            if r_link.status_code in (200, 204):
                link_deleted = True

        success = (link_deleted or not link_id) and (conn_deleted or not conn_id) and (link_deleted or conn_deleted)
        return DeleteResult(
            identifier=identifier,
            link_deleted=link_deleted,
            connector_deleted=conn_deleted,
            success=success,
            error=err_msg
        )
