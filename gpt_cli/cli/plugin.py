import argparse
import json
from typing import Any

from ..plugins.manager import PluginManager, PluginInfo


def _plugin_output(plugin: PluginInfo, status: str) -> dict[str, Any]:
    return {
        "status": status,
        "name": plugin.name,
        "connector_id": plugin.connector_id,
        "link_id": plugin.link_id,
        "auth_status": plugin.auth_status,
        "permission": plugin.apps_privacy_control,
        "actions": plugin.actions,
    }


def handle_plugin(args: argparse.Namespace, plugin_manager_cls=PluginManager) -> None:
    manager = plugin_manager_cls()
    if args.plugin_action == "list":
        plugins = manager.list_plugins()
        out = {"plugins": [_plugin_output(plugin, "connected") for plugin in plugins]}
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        elif not plugins:
            print("No connected MCP plugins.")
        else:
            for plugin in plugins:
                print(
                    f"{plugin.name}: {plugin.auth_status}, {plugin.apps_privacy_control}, "
                    f"{len(plugin.actions)} actions\n  connector={plugin.connector_id}\n  link={plugin.link_id}"
                )
        return

    if args.plugin_action == "add":
        plugin = manager.add_mcp_plugin(
            name=args.name,
            mcp_url=args.mcp_url,
            description=args.description,
            permission=args.permission,
        )
        out = _plugin_output(plugin, "added")
    elif args.plugin_action == "permission":
        plugin = manager.set_permission(args.identifier, args.permission)
        out = _plugin_output(plugin, "permission_updated")
    elif args.plugin_action == "remove":
        result = manager.delete_plugin(args.identifier)
        out = {
            "status": "removed" if result.success else "remove_failed",
            "identifier": result.identifier,
            "link_deleted": result.link_deleted,
            "connector_deleted": result.connector_deleted,
            "error": result.error,
        }
        if not result.success:
            if args.json:
                print(json.dumps(out, ensure_ascii=False))
            raise SystemExit(1)
    else:
        raise ValueError(f"Unknown plugin action: {args.plugin_action}")

    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    elif args.plugin_action == "remove":
        print(
            f"{out['status']}: {out['identifier']}\n"
            f"  link_deleted={out['link_deleted']}\n"
            f"  connector_deleted={out['connector_deleted']}"
        )
    else:
        print(
            f"{out['status']}: {out['name']}\n"
            f"  connector={out['connector_id']}\n"
            f"  link={out['link_id']}\n"
            f"  permission={out['permission']}\n"
            f"  actions={len(out['actions'])}"
        )
